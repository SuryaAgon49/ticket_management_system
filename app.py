from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from werkzeug.utils import secure_filename
from datetime import datetime
import uuid
import os
import json
import re
import psycopg2 # Import psycopg2 for PostgreSQL
from psycopg2.extras import DictCursor # For dictionary-like row access
from dotenv import load_dotenv # Import load_dotenv
import sys # Import sys for stderr logging

load_dotenv() # Load environment variables from .env file

app = Flask(__name__)
# IMPORTANT: Change this in production to a strong, random key
# Use an environment variable for the secret key in production
app.secret_key = os.environ.get('SECRET_KEY', 'your-development-secret-key-here')

# --- File Upload Configuration ---
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx', 'xls', 'xlsx'}
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max file size

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

# Create upload directory if it doesn't exist (for local development, Render handles persistent storage differently)
# Note: On Render, this directory will be ephemeral. For persistent storage, consider using a cloud storage service.
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# --- Database Configuration ---
# Use DATABASE_URL provided by Render for PostgreSQL
# This line now correctly attempts to read the DATABASE_URL environment variable.
DATABASE_URL = os.environ.get('DATABASE_URL')

# Helper function to get a database connection
def get_db():
    if not DATABASE_URL:
        # Log this error prominently to stderr for Render logs
        print("ERROR: DATABASE_URL environment variable is not set. Please configure it on Render.", file=sys.stderr)
        raise ValueError("DATABASE_URL environment variable is not set. Please configure it on Render.")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        # Use DictCursor to access columns by name
        return conn
    except psycopg2.Error as e:
        # Log database connection errors prominently to stderr
        print(f"CRITICAL ERROR: Could not connect to database using DATABASE_URL: {e}", file=sys.stderr)
        # Re-raise a more specific error for easier debugging upstream
        raise ConnectionError(f"Database connection failed: {e}") from e

# Function to initialize the database schema and add a default admin user
def init_db():
    conn = None
    try:
        conn = get_db() # This might raise ConnectionError or ValueError
        cursor = conn.cursor(cursor_factory=DictCursor) # Use DictCursor for schema creation too

        # Create users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL,
                name TEXT NOT NULL,
                email TEXT
            )
        ''')

        # Create tickets table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                category TEXT NOT NULL,
                priority TEXT NOT NULL,
                status TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_by_name TEXT NOT NULL,
                assigned_to TEXT,
                assigned_to_name TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (created_by) REFERENCES users (id),
                FOREIGN KEY (assigned_to) REFERENCES users (id)
            )
        ''')

        # Create comments table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS comments (
                id TEXT PRIMARY KEY,
                ticket_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL,
                message TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                type TEXT NOT NULL, -- e.g., 'comment', 'status_update', 'assignment'
                FOREIGN KEY (ticket_id) REFERENCES tickets (id),
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')

        # Create attachments table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS attachments (
                id TEXT PRIMARY KEY,
                ticket_id TEXT NOT NULL,
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                FOREIGN KEY (ticket_id) REFERENCES tickets (id)
            )
        ''')

        # Create notifications table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                message TEXT NOT NULL,
                ticket_id TEXT,
                timestamp TEXT NOT NULL,
                read INTEGER NOT NULL, -- 0 for unread, 1 for read
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (ticket_id) REFERENCES tickets (id)
            )
        ''')

        # Create approval_history table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS approval_history (
                id TEXT PRIMARY KEY,
                ticket_id TEXT NOT NULL,
                approver TEXT NOT NULL,
                role TEXT NOT NULL,
                action TEXT NOT NULL, -- e.g., 'Approved', 'Rejected'
                timestamp TEXT NOT NULL,
                FOREIGN KEY (ticket_id) REFERENCES tickets (id)
            )
        ''')

        conn.commit()

        # Add default admin user if not exists
        cursor.execute("SELECT * FROM users WHERE id = 'admin'")
        if not cursor.fetchone():
            cursor.execute('''
                INSERT INTO users (id, username, password, role, name, email)
                VALUES (%s, %s, %s, %s, %s, %s)
            ''', ('admin', 'admin', 'admin123', 'Admin', 'System Administrator', 'admin@company.com'))
            conn.commit()
            print("Default admin user created.") # For debugging/initial setup confirmation
        print("Database schema initialized and default admin user checked/created successfully.")
    except (psycopg2.Error, ValueError, ConnectionError) as e:
        # Log critical database initialization errors to stderr
        print(f"CRITICAL ERROR: Database initialization failed during app startup: {e}", file=sys.stderr)
        if conn:
            conn.rollback() # Rollback in case of error
        # Re-raise the exception to prevent the app from starting if DB init fails
        raise
    finally:
        if conn:
            conn.close()


# Initialize the database on app startup
# This block is crucial. If init_db fails, the app won't start,
# and Render will report a 502 or deployment failure.
try:
    with app.app_context():
        init_db()
except Exception as e:
    # If database initialization fails, log it and exit the application process.
    # Render will then report a deployment error or a 502.
    print(f"Application failed to start due to a critical database initialization error: {e}", file=sys.stderr)
    sys.exit(1) # Exit with a non-zero status code to indicate failure


# --- Global Configurations (moved from in-memory data) ---
TICKET_CATEGORIES = {
    'Hardware Issue': 'direct_to_it',
    'Software Issue': 'direct_to_it',
    'Network Issue': 'direct_to_it',
    'Upgradation': 'approval_workflow',
    'New Equipment': 'approval_workflow',
    'License Request': 'approval_workflow',
    'Other': 'direct_to_it'
}

TICKET_STATUSES = ['Open', 'Pending HOD Approval', 'Pending IT Head Approval', 'Approved', 'In Progress', 'Resolved', 'Closed', 'Rejected']
TICKET_PRIORITIES = ['Low', 'Medium', 'High', 'Critical']

# --- Helper Functions ---

def allowed_file(filename):
    """Checks if a file's extension is allowed."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_current_user():
    """Fetches the current logged-in user's data from the database."""
    if 'user_id' in session:
        conn = None
        try:
            conn = get_db()
            cursor = conn.cursor(cursor_factory=DictCursor)
            cursor.execute("SELECT * FROM users WHERE id = %s", (session['user_id'],))
            user_data = cursor.fetchone()
            if user_data:
                return dict(user_data)  # Convert Row object to dictionary
        except (psycopg2.Error, ConnectionError, ValueError) as e:
            print(f"Database error getting current user: {e}", file=sys.stderr)
        finally:
            if conn:
                conn.close()
    return None

def has_permission(required_roles):
    """Checks if the current user has any of the required roles."""
    user = get_current_user()
    if not user:
        return False
    return user['role'] in required_roles

def add_notification(user_id, message, ticket_id=None):
    """Adds a notification for a specific user to the database."""
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO notifications (id, user_id, message, ticket_id, timestamp, read)
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', (str(uuid.uuid4()), user_id, message, ticket_id, datetime.now().isoformat(), 0))
        conn.commit()
    except (psycopg2.Error, ConnectionError, ValueError) as e:
        print(f"Database error adding notification: {e}", file=sys.stderr)
        flash('Error adding notification.', 'error')
    finally:
        if conn:
            conn.close()

def get_users_by_role(role):
    """Fetches a list of users by their role from the database."""
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT * FROM users WHERE role = %s", (role,))
        users_data = cursor.fetchall()
        return [dict(user) for user in users_data]
    except (psycopg2.Error, ConnectionError, ValueError) as e:
        print(f"Database error fetching users by role: {e}", file=sys.stderr)
        return []
    finally:
        if conn:
            conn.close()

def get_filtered_tickets(user):
    """
    Retrieves tickets from the database based on the user's role.
    This function now pushes filtering logic to the SQL query for efficiency.
    """
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=DictCursor)
        query = "SELECT * FROM tickets"
        params = []
        where_clauses = []

        if user['role'] == 'Admin' or user['role'] == 'IT Head':
            pass # Admin and IT Head see all tickets, no initial WHERE clause needed
        elif user['role'] == 'HOD':
            where_clauses.append("(created_by = %s OR status = 'Pending HOD Approval' OR assigned_to = %s)")
            params.extend([user['id'], user['id']])
        elif user['role'] == 'Technician':
            where_clauses.append("assigned_to = %s")
            params.append(user['id'])
        elif user['role'] == 'User':
            where_clauses.append("created_by = %s")
            params.append(user['id'])
        else:
            return [] # Unknown role, return no tickets

        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)

        query += " ORDER BY created_at DESC" # Order by creation date, newest first

        cursor.execute(query, params)
        tickets_data = cursor.fetchall()
        return [dict(t) for t in tickets_data]
    except (psycopg2.Error, ConnectionError, ValueError) as e:
        print(f"Database error fetching filtered tickets: {e}", file=sys.stderr)
        return []
    finally:
        if conn:
            conn.close()

def process_ticket_workflow(ticket_id, category, current_user_id):
    """
    Processes the ticket workflow, updating its status and notifying relevant parties
    directly in the database.
    """
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        current_time = datetime.now().isoformat()

        workflow = TICKET_CATEGORIES.get(category, 'direct_to_it')
        new_status = 'Open' # Default status

        if workflow == 'approval_workflow':
            new_status = 'Pending HOD Approval'
            # Notify all HODs
            hods = get_users_by_role('HOD')
            for hod in hods:
                add_notification(hod['id'],
                               f"New '{category}' request requires approval: Ticket {ticket_id}",
                               ticket_id)
        else:
            # Direct to IT Head for technical issues
            new_status = 'Open'
            # Notify IT Heads
            it_heads = get_users_by_role('IT Head')
            for it_head in it_heads:
                add_notification(it_head['id'],
                               f"New '{category}' ticket: Ticket {ticket_id}",
                               ticket_id)

        # Update ticket status in DB
        cursor.execute("UPDATE tickets SET status = %s, updated_at = %s WHERE id = %s",
                       (new_status, current_time, ticket_id))
        conn.commit()
    except (psycopg2.Error, ConnectionError, ValueError) as e:
        print(f"Database error processing ticket workflow: {e}", file=sys.stderr)
        flash('Error processing ticket workflow.', 'error')
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


def get_dashboard_stats(user):
    """
    Retrieves dashboard statistics directly from the database based on user role.
    Handles cases where COUNT(*) queries might return no rows by defaulting to 0.
    """
    conn = None
    stats = {}
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # Base conditions and parameters for the user's view
        base_conditions = []
        base_params = []

        if user['role'] == 'HOD':
            base_conditions.append("(created_by = %s OR status = 'Pending HOD Approval' OR assigned_to = %s)")
            base_params.extend([user['id'], user['id']])
        elif user['role'] == 'Technician':
            base_conditions.append("assigned_to = %s")
            base_params.append(user['id'])
        elif user['role'] == 'User':
            base_conditions.append("created_by = %s")
            base_params.append(user['id'])
        # Admin and IT Head roles don't add specific conditions here for the "base" query
        # as they see all tickets initially.

        # Function to build dynamic queries with optional WHERE clause
        def build_query(status_condition=None):
            query_parts = ["SELECT COUNT(*) FROM tickets"]
            current_params = list(base_params) # Copy base_params for each query

            if base_conditions:
                query_parts.append("WHERE " + " AND ".join(base_conditions))
            
            if status_condition:
                if not base_conditions: # If no base conditions, start with WHERE
                    query_parts.append("WHERE " + status_condition)
                else: # If base conditions exist, append with AND
                    query_parts.append("AND " + status_condition)
            
            return " ".join(query_parts), current_params

        # Helper to safely fetch count
        def fetch_count(query_info):
            sql_query = query_info[0]
            params = None
            if len(query_info) > 1:
                # If query_info has a second element, it must be the parameters
                params = query_info[1]
                # Ensure params is not an empty list if psycopg2 prefers None for no params
                if params and not isinstance(params, (list, tuple)):
                    # This case should ideally not happen if build_query is correct,
                    # but adding a safeguard.
                    print(f"WARNING: Expected list/tuple for params, got {type(params)}. Setting to None.", file=sys.stderr)
                    params = None
                elif not params: # If params list/tuple is empty, set to None
                    params = None

            try:
                if params is not None:
                    cursor.execute(sql_query, params)
                else:
                    cursor.execute(sql_query)
                
                result = cursor.fetchone()
                return result[0] if result else 0
            except Exception as e:
                print(f"ERROR in fetch_count: {e}", file=sys.stderr)
                # Log the full query and params for better debugging if this still fails
                print(f"Failed query: {sql_query}, Params: {params}", file=sys.stderr)
                return 0 # Return 0 on error

        # Total tickets for the user's view
        stats['total_tickets'] = fetch_count(build_query())

        # Specific status counts for the user's view
        stats['open_tickets'] = fetch_count(build_query("status = 'Open'"))
        stats['in_progress_tickets'] = fetch_count(build_query("status = 'In Progress'"))
        stats['resolved_tickets'] = fetch_count(build_query("status = 'Resolved'"))
        stats['closed_tickets'] = fetch_count(build_query("status = 'Closed'"))
        stats['pending_approval'] = fetch_count(build_query("status LIKE 'Pending%'"))


        if user['role'] in ['Admin', 'HOD', 'IT Head']:
            # Additional stats for management roles (system-wide or role-specific)
            # For these, build_query is not used, so we manually create the query_info tuple
            stats['total_system_tickets'] = fetch_count(("SELECT COUNT(*) FROM tickets",))
            stats['pending_hod_approval'] = fetch_count(("SELECT COUNT(*) FROM tickets WHERE status = 'Pending HOD Approval'",))
            stats['pending_it_approval'] = fetch_count(("SELECT COUNT(*) FROM tickets WHERE status = 'Pending IT Head Approval'",))
            stats['approved_tickets'] = fetch_count(("SELECT COUNT(*) FROM tickets WHERE status = 'Approved'",))
            stats['rejected_tickets'] = fetch_count(("SELECT COUNT(*) FROM tickets WHERE status = 'Rejected'",))

    except (psycopg2.Error, ConnectionError, ValueError) as e:
        print(f"Database error fetching dashboard stats: {e}", file=sys.stderr)
        flash('Error fetching dashboard statistics.', 'error')
        # Return default zero stats on error
        return {
            'total_tickets': 0, 'open_tickets': 0, 'in_progress_tickets': 0,
            'resolved_tickets': 0, 'closed_tickets': 0, 'pending_approval': 0,
            'total_system_tickets': 0, 'pending_hod_approval': 0,
            'pending_it_approval': 0, 'approved_tickets': 0, 'rejected_tickets': 0
        }
    finally:
        if conn:
            conn.close()
    return stats

# --- Routes ---

@app.route('/')
def index():
    """Landing page."""
    # Pass datetime.now() and user to the template
    return render_template('index.html', now=datetime.now(), user=get_current_user())

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handles user login."""
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = None
        try:
            conn = get_db()
            cursor = conn.cursor(cursor_factory=DictCursor)
            cursor.execute("SELECT * FROM users WHERE username = %s AND password = %s", (username, password))
            user_data = cursor.fetchone()

            if user_data:
                session['user_id'] = user_data['id']
                flash(f'Welcome, {user_data["name"]}!', 'success')
                return redirect(url_for('dashboard'))
            else:
                flash('Invalid username or password', 'error')
        except (psycopg2.Error, ConnectionError, ValueError) as e:
            print(f"Database error during login: {e}", file=sys.stderr)
            flash('An error occurred during login. Please try again.', 'error')
        finally:
            if conn:
                conn.close()

    # Pass datetime.now() and user to the template
    return render_template('login.html', now=datetime.now(), user=get_current_user())

@app.route('/logout')
def logout():
    """Logs out the current user."""
    session.pop('user_id', None)
    flash('You have been logged out', 'info')
    return redirect(url_for('index'))

@app.route('/dashboard')
def dashboard():
    """Displays the role-specific dashboard."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    user_tickets = get_filtered_tickets(user)
    
    conn = None
    user_notifications = []
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT * FROM notifications WHERE user_id = %s ORDER BY timestamp DESC LIMIT 3", (user['id'],))
        user_notifications = [dict(n) for n in cursor.fetchall()]
    except (psycopg2.Error, ConnectionError, ValueError) as e:
        print(f"Database error fetching notifications for dashboard: {e}", file=sys.stderr)
        flash('Error loading notifications for dashboard.', 'error')
    finally:
        if conn:
            conn.close()

    stats = get_dashboard_stats(user)

    # Pass datetime.now() and user to the template
    return render_template('dashboard.html',
                           user=user,
                           tickets=user_tickets[:5], # Show top 5 recent tickets
                           stats=stats,
                           notifications=user_notifications,
                           now=datetime.now())

@app.route('/create_ticket', methods=['GET', 'POST'])
def create_ticket():
    """Allows a 'User' role to create new tickets."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    if not has_permission(['User']):
        flash('Access denied. Only Users can create tickets.', 'error')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        conn = None
        try:
            conn = get_db()
            cursor = conn.cursor()
            ticket_id = str(uuid.uuid4())
            current_time = datetime.now().isoformat()

            # Insert ticket into database
            cursor.execute('''
                INSERT INTO tickets (id, title, description, category, priority, status, created_by, created_by_name, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (
                ticket_id,
                request.form['title'],
                request.form['description'],
                request.form['category'],
                request.form['priority'],
                'Open',  # Initial status before workflow processing
                user['id'],
                user['name'],
                current_time,
                current_time
            ))
            conn.commit()

            # Handle file upload
            if 'attachments' in request.files:
                files = request.files.getlist('attachments')
                for file in files:
                    if file and file.filename != '' and allowed_file(file.filename):
                        filename = secure_filename(file.filename)
                        unique_filename = f"{uuid.uuid4()}_{filename}"
                        file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                        file.save(file_path)
                        cursor.execute('''
                            INSERT INTO attachments (id, ticket_id, original_name, stored_name, file_path)
                            VALUES (%s, %s, %s, %s, %s)
                        ''', (str(uuid.uuid4()), ticket_id, filename, unique_filename, file_path))
                        conn.commit()

            # Process workflow based on category (this function now updates the DB directly)
            process_ticket_workflow(ticket_id, request.form['category'], user['id'])

            flash('Ticket created successfully!', 'success')
            return redirect(url_for('ticket_list'))
        except (psycopg2.Error, ConnectionError, ValueError) as e:
            print(f"Database error creating ticket: {e}", file=sys.stderr)
            flash('An error occurred while creating the ticket. Please try again.', 'error')
            # Rollback in case of error
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

    # Pass datetime.now() and user to the template
    return render_template('create_ticket.html',
                           user=user,
                           priorities=TICKET_PRIORITIES,
                           categories=list(TICKET_CATEGORIES.keys()),
                           now=datetime.now())

@app.route('/tickets')
def ticket_list():
    """Displays a list of tickets, filtered by user role and optional status."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    conn = None
    tickets = []
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=DictCursor)
        query = "SELECT * FROM tickets WHERE 1=1"
        params = []

        # Apply role-based filtering first
        if user['role'] == 'Admin' or user['role'] == 'IT Head':
            # No additional WHERE clause for Admin/IT Head
            pass
        elif user['role'] == 'HOD':
            query += " AND (created_by = %s OR status = 'Pending HOD Approval' OR assigned_to = %s)"
            params.extend([user['id'], user['id']])
        elif user['role'] == 'Technician':
            query += " AND assigned_to = %s"
            params.append(user['id'])
        elif user['role'] == 'User':
            query += " AND created_by = %s"
            params.append(user['id'])
        else:
            # Unknown role, return no tickets
            return render_template('ticket_list.html', user=user, tickets=[], statuses=TICKET_STATUSES, current_filter=None, now=datetime.now())

        # Apply status filter if requested
        status_filter = request.args.get('status')
        if status_filter and status_filter in TICKET_STATUSES: # Validate status
            query += " AND status = %s"
            params.append(status_filter)
        elif status_filter == 'All': # Allow 'All' to clear filter
            status_filter = None

        query += " ORDER BY created_at DESC" # Always order by creation date, newest first

        cursor.execute(query, params)
        tickets_data = cursor.fetchall()
        tickets = [dict(t) for t in tickets_data]
    except (psycopg2.Error, ConnectionError, ValueError) as e:
        print(f"Database error fetching ticket list: {e}", file=sys.stderr)
        flash('Error loading tickets.', 'error')
        tickets = []
    finally:
        if conn:
            conn.close()

    # Pass datetime.now() and user to the template
    return render_template('ticket_list.html',
                           user=user,
                           tickets=tickets,
                           statuses=TICKET_STATUSES,
                           current_filter=status_filter,
                           now=datetime.now())

@app.route('/ticket/<ticket_id>')
def ticket_detail(ticket_id):
    """Displays detailed information for a specific ticket and allows management actions."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    conn = None
    ticket = None
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,))
        ticket_data = cursor.fetchone()
        if not ticket_data:
            flash('Ticket not found', 'error')
            return redirect(url_for('ticket_list'))

        ticket = dict(ticket_data)

        # Check if the user has permission to view this specific ticket
        can_view = False
        if user['role'] in ['Admin', 'IT Head']:
            can_view = True
        elif user['role'] == 'User' and ticket['created_by'] == user['id']:
            can_view = True
        elif user['role'] == 'Technician' and ticket.get('assigned_to') == user['id']:
            can_view = True
        elif user['role'] == 'HOD' and (ticket['created_by'] == user['id'] or
                                        ticket['status'] == 'Pending HOD Approval' or
                                        ticket.get('assigned_to') == user['id']):
            can_view = True

        if not can_view:
            flash('Access denied. You cannot view this ticket.', 'error')
            return redirect(url_for('ticket_list'))

        # Fetch comments, attachments, and approval history from DB
        cursor.execute("SELECT * FROM comments WHERE ticket_id = %s ORDER BY timestamp ASC", (ticket_id,))
        ticket['comments'] = [dict(c) for c in cursor.fetchall()]
        
        cursor.execute("SELECT * FROM attachments WHERE ticket_id = %s", (ticket_id,))
        ticket['attachments'] = [dict(a) for a in cursor.fetchall()]
        
        cursor.execute("SELECT * FROM approval_history WHERE ticket_id = %s ORDER BY timestamp ASC", (ticket_id,))
        ticket['approval_history'] = [dict(h) for h in cursor.fetchall()]

        technicians = get_users_by_role('Technician')

        # Pass datetime.now() and TICKET_STATUSES to the template
        return render_template('ticket_detail.html',
                               user=user,
                               ticket=ticket,
                               statuses=TICKET_STATUSES,
                               technicians=technicians,
                               now=datetime.now())
    except (psycopg2.Error, ConnectionError, ValueError) as e:
        print(f"Database error fetching ticket detail: {e}", file=sys.stderr)
        flash('An error occurred loading ticket details.', 'error')
        return redirect(url_for('ticket_list'))
    finally:
        if conn:
            conn.close()


@app.route('/approve_ticket/<ticket_id>/<action>')
def approve_ticket(ticket_id, action):
    """Handles HOD/IT Head approval or rejection of tickets."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,))
        ticket_data = cursor.fetchone()
        if not ticket_data:
            flash('Ticket not found', 'error')
            return redirect(url_for('ticket_list'))

        ticket = dict(ticket_data)
        new_status = ticket['status'] # Initialize with current status
        
        cursor_for_insert = conn.cursor() # Use a regular cursor for inserts if DictCursor causes issues with executemany

        # HOD approval logic
        if user['role'] == 'HOD' and ticket['status'] == 'Pending HOD Approval':
            if action == 'approve':
                new_status = 'Pending IT Head Approval'
                cursor_for_insert.execute('''
                    INSERT INTO approval_history (id, ticket_id, approver, role, action, timestamp)
                    VALUES (%s, %s, %s, %s, %s, %s)
                ''', (str(uuid.uuid4()), ticket_id, user['name'], 'HOD', 'Approved', datetime.now().isoformat()))

                # Notify IT Heads
                it_heads = get_users_by_role('IT Head')
                for it_head in it_heads:
                    add_notification(it_head['id'],
                                   f"Ticket {ticket['id']} approved by HOD, awaiting IT Head approval",
                                   ticket['id'])
                flash('Ticket approved by HOD, pending IT Head approval.', 'success')
            elif action == 'reject':
                new_status = 'Rejected'
                cursor_for_insert.execute('''
                    INSERT INTO approval_history (id, ticket_id, approver, role, action, timestamp)
                    VALUES (%s, %s, %s, %s, %s, %s)
                ''', (str(uuid.uuid4()), ticket_id, user['name'], 'HOD', 'Rejected', datetime.now().isoformat()))

                # Notify ticket creator
                add_notification(ticket['created_by'],
                               f"Ticket {ticket['id']} rejected by HOD",
                               ticket['id'])
                flash('Ticket rejected by HOD.', 'info')
            else:
                flash('Invalid action for HOD approval.', 'error')
                return redirect(url_for('ticket_detail', ticket_id=ticket_id))

        # IT Head approval logic
        elif user['role'] == 'IT Head' and ticket['status'] == 'Pending IT Head Approval':
            if action == 'approve':
                new_status = 'Approved'
                cursor_for_insert.execute('''
                    INSERT INTO approval_history (id, ticket_id, approver, role, action, timestamp)
                    VALUES (%s, %s, %s, %s, %s, %s)
                ''', (str(uuid.uuid4()), ticket_id, user['name'], 'IT Head', 'Approved', datetime.now().isoformat()))

                # Notify ticket creator
                add_notification(ticket['created_by'],
                               f"Ticket {ticket['id']} approved by IT Head",
                               ticket['id'])
                flash('Ticket approved by IT Head.', 'success')
            elif action == 'reject':
                new_status = 'Rejected'
                cursor_for_insert.execute('''
                    INSERT INTO approval_history (id, ticket_id, approver, role, action, timestamp)
                    VALUES (%s, %s, %s, %s, %s, %s)
                ''', (str(uuid.uuid4()), ticket_id, user['name'], 'IT Head', 'Rejected', datetime.now().isoformat()))

                # Notify ticket creator
                add_notification(ticket['created_by'],
                               f"Ticket {ticket['id']} rejected by IT Head",
                               ticket['id'])
                flash('Ticket rejected by IT Head.', 'info')
            else:
                flash('Invalid action for IT Head approval.', 'error')
                return redirect(url_for('ticket_detail', ticket_id=ticket_id))
        else:
            flash('Access denied or invalid ticket status for approval.', 'error')
            return redirect(url_for('ticket_detail', ticket_id=ticket_id))

        # Update ticket status in the database
        cursor_for_insert.execute("UPDATE tickets SET status = %s, updated_at = %s WHERE id = %s",
                       (new_status, datetime.now().isoformat(), ticket_id))
        conn.commit()

    except (psycopg2.Error, ConnectionError, ValueError) as e:
        print(f"Database error during approval: {e}", file=sys.stderr)
        flash('An error occurred during the approval process. Please try again.', 'error')
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

    return redirect(url_for('ticket_detail', ticket_id=ticket_id))

@app.route('/assign_ticket/<ticket_id>', methods=['POST'])
def assign_ticket(ticket_id):
    """Allows IT Heads and Admins to assign a ticket to a technician."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    if not has_permission(['IT Head', 'Admin']):
        flash('Access denied. Only IT Heads and Admins can assign tickets.', 'error')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))

    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,))
        ticket_data = cursor.fetchone()
        if not ticket_data:
            flash('Ticket not found', 'error')
            return redirect(url_for('ticket_list'))

        ticket = dict(ticket_data)

        assigned_to_id = request.form.get('assigned_to')
        if assigned_to_id:
            cursor.execute("SELECT * FROM users WHERE id = %s", (assigned_to_id,))
            assigned_user_data = cursor.fetchone()
            if assigned_user_data and assigned_user_data['role'] == 'Technician':
                assigned_user = dict(assigned_user_data)

                # Update ticket assignment and status
                cursor.execute("UPDATE tickets SET assigned_to = %s, assigned_to_name = %s, status = %s, updated_at = %s WHERE id = %s",
                               (assigned_user['id'], assigned_user['name'], 'In Progress', datetime.now().isoformat(), ticket_id))

                # Add an assignment comment
                cursor.execute('''
                    INSERT INTO comments (id, ticket_id, user_id, user_name, message, timestamp, type)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                ''', (str(uuid.uuid4()), ticket_id, user['id'], user['name'],
                      f"Ticket assigned to {assigned_user['name']}", datetime.now().isoformat(), 'assignment'))
                conn.commit()

                # Notify the assigned technician
                add_notification(assigned_user['id'],
                               f"New ticket assigned to you: {ticket['title']}",
                               ticket['id'])

                flash('Ticket assigned successfully!', 'success')
            else:
                flash('Invalid technician selected.', 'error')
        else:
            flash('No technician selected.', 'error')
    except (psycopg2.Error, ConnectionError, ValueError) as e:
        print(f"Database error during assignment: {e}", file=sys.stderr)
        flash('An error occurred during assignment. Please try again.', 'error')
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

    return redirect(url_for('ticket_detail', ticket_id=ticket_id))

@app.route('/update_ticket/<ticket_id>', methods=['POST'])
def update_ticket(ticket_id):
    """Allows technicians to update ticket status and any user to add comments."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,))
        ticket_data = cursor.fetchone()
        if not ticket_data:
            flash('Ticket not found', 'error')
            return redirect(url_for('ticket_list'))

        ticket = dict(ticket_data)
        current_time = datetime.now().isoformat()

        # Handle status update (Technician only)
        if 'status' in request.form and user['role'] == 'Technician':
            if ticket.get('assigned_to') != user['id']:
                flash('You can only update tickets assigned to you.', 'error')
                return redirect(url_for('ticket_detail', ticket_id=ticket_id))

            old_status = ticket['status']
            new_status = request.form['status']

            if new_status not in TICKET_STATUSES: # Basic validation
                flash('Invalid status provided.', 'error')
                return redirect(url_for('ticket_detail', ticket_id=ticket_id))

            cursor.execute("UPDATE tickets SET status = %s, updated_at = %s WHERE id = %s",
                           (new_status, current_time, ticket_id))

            # Add a status update comment
            cursor.execute('''
                INSERT INTO comments (id, ticket_id, user_id, user_name, message, timestamp, type)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', (str(uuid.uuid4()), ticket_id, user['id'], user['name'],
                  f"Status changed from '{old_status}' to '{new_status}'", current_time, 'status_update'))
            conn.commit()

            # Notify ticket creator
            add_notification(ticket['created_by'],
                            f"Ticket {ticket['id']} status updated to {new_status}",
                            ticket['id'])

            flash('Ticket status updated successfully!', 'success')

        # Handle comment addition (any authenticated user)
        elif 'comment' in request.form and request.form['comment'].strip():
            comment_message = request.form['comment'].strip()
            cursor.execute('''
                INSERT INTO comments (id, ticket_id, user_id, user_name, message, timestamp, type)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', (str(uuid.uuid4()), ticket_id, user['id'], user['name'],
                  comment_message, current_time, 'comment'))
            cursor.execute("UPDATE tickets SET updated_at = %s WHERE id = %s", (current_time, ticket_id))
            conn.commit()

            # Notify relevant users (creator and assigned technician, if different from current user)
            notify_users = set()
            if ticket['created_by'] != user['id']:
                notify_users.add(ticket['created_by'])
            if ticket.get('assigned_to') and ticket['assigned_to'] != user['id']:
                notify_users.add(ticket['assigned_to'])

            for notify_user_id in notify_users:
                add_notification(notify_user_id,
                               f"New comment on ticket {ticket['id']} by {user['name']}",
                               ticket['id'])

            flash('Comment added successfully!', 'success')
        else:
            flash('No valid action performed (status update or comment).', 'error')

    except (psycopg2.Error, ConnectionError, ValueError) as e:
        print(f"Database error during ticket update: {e}", file=sys.stderr)
        flash('An error occurred during ticket update. Please try again.', 'error')
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

    return redirect(url_for('ticket_detail', ticket_id=ticket_id))

@app.route('/download_attachment/<ticket_id>/<stored_name>')
def download_attachment(ticket_id, stored_name):
    """Allows downloading of ticket attachments."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=DictCursor)
        # Verify ticket exists and user has access
        cursor.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,))
        ticket_data = cursor.fetchone()
        if not ticket_data:
            flash('Ticket not found.', 'error')
            return redirect(url_for('ticket_list'))

        ticket = dict(ticket_data)

        # Re-check access permissions for downloading (similar to ticket_detail)
        can_download = False
        if user['role'] in ['Admin', 'IT Head']:
            can_download = True
        elif user['role'] == 'User' and ticket['created_by'] == user['id']:
            can_download = True
        elif user['role'] == 'Technician' and ticket.get('assigned_to') == user['id']:
            can_download = True
        elif user['role'] == 'HOD' and (ticket['created_by'] == user['id'] or
                                        ticket['status'] == 'Pending HOD Approval' or
                                        ticket.get('assigned_to') == user['id']):
            can_download = True

        if not can_download:
            flash('Access denied. You cannot download attachments for this ticket.', 'error')
            return redirect(url_for('ticket_list'))

        cursor.execute("SELECT * FROM attachments WHERE ticket_id = %s AND stored_name = %s", (ticket_id, stored_name))
        attachment_data = cursor.fetchone()
        if not attachment_data:
            flash('Attachment not found.', 'error')
            return redirect(url_for('ticket_detail', ticket_id=ticket_id))

        attachment = dict(attachment_data)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], attachment['stored_name']) # Ensure correct path
        
        if os.path.exists(file_path):
            return send_file(file_path, as_attachment=True, download_name=attachment['original_name'])
        else:
            flash('File not found on server.', 'error')
            return redirect(url_for('ticket_detail', ticket_id=ticket_id))
    except (psycopg2.Error, ConnectionError, ValueError) as e:
        print(f"Database error during attachment download: {e}", file=sys.stderr)
        flash('An error occurred during download. Please try again.', 'error')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))
    finally:
        if conn:
            conn.close()

@app.route('/users')
def user_list():
    """Displays a list of all users for management roles."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    if not has_permission(['Admin', 'HOD', 'IT Head']):
        flash('Access denied. You do not have permission to view users.', 'error')
        return redirect(url_for('dashboard'))

    conn = None
    all_users = []
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT * FROM users ORDER BY name ASC")
        all_users_data = cursor.fetchall()
        all_users = [dict(u) for u in all_users_data]
    except (psycopg2.Error, ConnectionError, ValueError) as e:
        print(f"Database error fetching user list: {e}", file=sys.stderr)
        flash('Error loading user list.', 'error')
        all_users = []
    finally:
        if conn:
            conn.close()

    # Pass datetime.now() and user to the template
    return render_template('user_list.html',
                           user=user,
                           users=all_users,
                           now=datetime.now())

@app.route('/create_user', methods=['GET', 'POST'])
def create_user():
    """Allows authorized roles to create new users with role-based restrictions."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    # Define which roles the current user can create
    allowed_roles_for_creation = []
    if user['role'] == 'Admin':
        allowed_roles_for_creation = ['Admin', 'HOD', 'IT Head', 'Technician', 'User']
    elif user['role'] == 'HOD':
        allowed_roles_for_creation = ['User']
    elif user['role'] == 'IT Head':
        allowed_roles_for_creation = ['Technician']
    else:
        flash('Access denied. You do not have permission to create users.', 'error')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        name = request.form['name']
        email = request.form['email']
        role = request.form['role']

        # Validate if the selected role is allowed for the current user
        if role not in allowed_roles_for_creation:
            flash('You cannot create users with that role.', 'error')
            # Pass datetime.now() to the template if rendering form again
            return render_template('create_user.html', user=user, roles=allowed_roles_for_creation, now=datetime.now())

        conn = None
        try:
            conn = get_db()
            cursor = conn.cursor(cursor_factory=DictCursor)
            cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
            existing_user = cursor.fetchone()
            if existing_user:
                flash('Username already exists. Please choose a different one.', 'error')
            else:
                new_user_id = str(uuid.uuid4())
                cursor.execute('''
                    INSERT INTO users (id, username, password, role, name, email)
                    VALUES (%s, %s, %s, %s, %s, %s)
                ''', (new_user_id, username, password, role, name, email))
                conn.commit()
                flash('User created successfully!', 'success')
                return redirect(url_for('user_list'))
        except (psycopg2.Error, ConnectionError, ValueError) as e:
            print(f"Database error creating user: {e}", file=sys.stderr)
            flash('An error occurred while creating the user. Please try again.', 'error')
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

    # Pass datetime.now() and user to the template
    return render_template('create_user.html',
                           user=user,
                           roles=allowed_roles_for_creation,
                           now=datetime.now())

@app.route('/notifications')
def notifications_page():
    """Displays all notifications for the current user."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    conn = None
    user_notifications = []
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT * FROM notifications WHERE user_id = %s ORDER BY timestamp DESC", (user['id'],))
        
        # Convert timestamp strings to datetime objects for consistent handling in templates
        user_notifications_data = cursor.fetchall()
        for n in user_notifications_data:
            notification = dict(n)
            try:
                # Attempt to convert timestamp to datetime object
                notification['timestamp'] = datetime.fromisoformat(notification['timestamp'])
            except (ValueError, TypeError):
                # If conversion fails, keep it as is or handle as appropriate (e.g., set to None)
                print(f"Warning: Could not convert timestamp '{notification['timestamp']}' to datetime for notification {notification['id']}", file=sys.stderr)
            user_notifications.append(notification)

    except (psycopg2.Error, ConnectionError, ValueError) as e:
        print(f"Database error fetching notifications: {e}", file=sys.stderr)
        flash('Error loading notifications.', 'error')
        user_notifications = []
    finally:
        if conn:
            conn.close()

    # Pass datetime.now() and user to the template
    return render_template('notifications.html',
                           user=user,
                           notifications=user_notifications,
                           now=datetime.now())

@app.route('/mark_notification_read/<notification_id>')
def mark_notification_read(notification_id):
    """Marks a specific notification as read."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=DictCursor)
        # Ensure the notification belongs to the current user
        cursor.execute("SELECT * FROM notifications WHERE id = %s AND user_id = %s", (notification_id, user['id']))
        notification = cursor.fetchone()
        if notification:
            cursor.execute("UPDATE notifications SET read = 1 WHERE id = %s", (notification_id,))
            conn.commit()
    except (psycopg2.Error, ConnectionError, ValueError) as e:
        print(f"Database error marking notification as read: {e}", file=sys.stderr)
        flash('Error marking notification as read.', 'error')
    finally:
        if conn:
            conn.close()

    return redirect(url_for('notifications_page'))

# --- API Endpoints ---

@app.route('/api/ticket_stats')
def api_ticket_stats():
    """API endpoint to get ticket statistics for the current user."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Not authenticated'}), 401

    stats = get_dashboard_stats(user)
    return jsonify(stats)

@app.route('/api/notifications/unread_count')
def api_unread_notifications():
    """API endpoint to get the count of unread notifications for the current user."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Not authenticated'}), 401

    conn = None
    unread_count = 0
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM notifications WHERE user_id = %s AND read = 0", (user['id'],))
        unread_count_data = cursor.fetchone()
        unread_count = unread_count_data[0] if unread_count_data else 0
    except (psycopg2.Error, ConnectionError, ValueError) as e:
        print(f"Database error fetching unread notification count: {e}", file=sys.stderr)
        unread_count = 0 # Default to 0 on error
    finally:
        if conn:
            conn.close()

    return jsonify({'unread_count': unread_count})

# --- Error Handlers ---

@app.errorhandler(404)
def not_found(error):
    """Handles 404 Not Found errors."""
    # Pass datetime.now() and user to the template
    return render_template('404.html', now=datetime.now(), user=get_current_user()), 404

@app.errorhandler(500)
def internal_error(error):
    """Handles 500 Internal Server Errors."""
    # Pass datetime.now() and user to the template
    return render_template('500.html', now=datetime.now(), user=get_current_user()), 500

@app.errorhandler(413)
def too_large(error):
    """Handles file too large errors (HTTP 413)."""
    flash('File too large. Maximum size is 16MB.', 'error')
    return redirect(request.url)

# --- Jinja2 Template Filters ---

@app.template_filter('datetime')
def datetime_filter(dt_str):
    """Formats datetime for display (handles ISO format string from DB)."""
    if isinstance(dt_str, str):
        try:
            dt = datetime.fromisoformat(dt_str)
        except ValueError:
            # Handle cases where the string might not be a valid ISO format
            return dt_str # Return original string if conversion fails
    else:  # Assume it's already a datetime object
        dt = dt_str
    
    if isinstance(dt, datetime):
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    return dt_str # Fallback if not a datetime object after all

@app.template_filter('date')
def date_filter(dt_str):
    """Formats date for display (handles ISO format string from DB)."""
    if isinstance(dt_str, str):
        try:
            dt = datetime.fromisoformat(dt_str)
        except ValueError:
            return dt_str # Return original string if conversion fails
    else:  # Assume it's already a datetime object
        dt = dt_str
    
    if isinstance(dt, datetime):
        return dt.strftime('%Y-%m-%d')
    return dt_str # Fallback if not a datetime object after all

@app.template_filter('time_ago')
def time_ago_filter(dt_str):
    """Shows time ago format (handles ISO format string from DB)."""
    if isinstance(dt_str, str):
        try:
            dt = datetime.fromisoformat(dt_str)
        except ValueError:
            return dt_str # Return original string if conversion fails
    else:  # Assume it's already a datetime object
        dt = dt_str

    if not isinstance(dt, datetime):
        return dt_str # Fallback if not a datetime object

    now = datetime.now()
    diff = now - dt

    if diff.days > 0:
        return f"{diff.days} days ago"
    elif diff.seconds > 3600:
        hours = diff.seconds // 3600
        return f"{hours} hours ago"
    elif diff.seconds > 60:
        minutes = diff.seconds // 60
        return f"{minutes} minutes ago"
    else:
        return "Just now"

# Add this new filter:
@app.template_filter('regex_search')
def regex_search_filter(s, pattern):
    match = re.search(pattern, s)
    return match.group(0) if match else None

if __name__ == '__main__':
    # This block is for local development only.
    # On Render, a WSGI server (like Gunicorn) will run your app.
    # For local testing, ensure DATABASE_URL is set (e.g., via a .env file or directly in your shell)
    # Example: export DATABASE_URL="postgresql://user:password@host:port/database"
    app.run(debug=True, host='0.0.0.0', port=5000)
