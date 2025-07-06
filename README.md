Ticket Management System
A comprehensive web application built with Flask for managing support tickets, featuring role-based access control, approval workflows, and notification functionalities. This system is designed to streamline communication and task management within an organization, allowing users to raise tickets, technicians to resolve them, and management to oversee the process.

Features
User Authentication & Authorization: Secure login system with distinct roles (Admin, HOD, IT Head, Technician, User).

Role-Based Access Control (RBAC): Different functionalities and data visibility based on user roles.

Ticket Creation: Users can create new support tickets with details like title, description, category, and priority.

File Attachments: Attach documents or images to tickets during creation.

Dynamic Workflows: Tickets are routed based on their category (e.g., direct to IT, or requiring HOD/IT Head approval).

Ticket Assignment: IT Heads/Admins can assign tickets to specific technicians.

Status Updates: Technicians can update ticket statuses (e.g., In Progress, Resolved, Closed).

Commenting System: Users, technicians, and managers can add comments to tickets.

Approval History: Track the approval/rejection journey of tickets requiring management sign-off.

Notifications: Real-time notifications for relevant actions (new tickets, assignments, status changes, comments, approvals).

Dashboard: Role-specific dashboards providing an overview of ticket statistics and recent activity.

User Management: Admins and other authorized roles can create and manage user accounts.

Responsive Design: (Assuming your templates are responsive) User-friendly interface across various devices.

Technologies Used
Backend: Flask (Python Web Framework)

Database: PostgreSQL (via psycopg2)

Dependency Management: pip

Web Server (Production): Gunicorn

Environment Variables: python-dotenv (for local development)

Deployment Platform: Render

Local Setup
Follow these steps to get the application running on your local machine for development and testing.

Prerequisites
Python 3.8+ installed

A PostgreSQL database instance (you can run one locally using Docker, or use a free tier cloud database for testing).

1. Clone the Repository
git clone <your-repository-url>
cd <your-project-directory>

2. Set up a Virtual Environment
It's highly recommended to use a virtual environment to manage project dependencies.

python -m venv venv
# On Windows:
.\venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

3. Install Dependencies
Install all required Python packages using pip:

pip install -r requirements.txt

4. Configure Environment Variables (.env file)
Create a file named .env in the root directory of your project (the same directory as app.py). This file will store sensitive information and database connection strings for local development.

Important: Do not commit this file to your Git repository! Add .env to your .gitignore.

# .env file for local development
# This file should NOT be committed to version control (e.g., Git) in production environments.

# Your PostgreSQL Database URL
# For LOCAL development, use the EXTERNAL Database URL provided by your PostgreSQL provider (e.g., Render).
# Example for Render: postgresql://ticket_user:0yP70jBvjcHVRQw0Zf6FZlpL054BArpf@dpg-d1l3p27diees73f5un4g-a.oregon-postgres.render.com/ticket_xqd0
DATABASE_URL="postgresql://ticket_user:0yP70jBvjcHVRQw0Zf6FZlpL054BArpf@dpg-d1l3p27diees73f5un4g-a.oregon-postgres.render.com/ticket_xqd0"

# Your Flask Secret Key
# Generate a strong, random key for production. For development, this can be a placeholder.
# You can generate one using: python -c "import os; print(os.urandom(24).hex())"
SECRET_KEY="a4a3ba4cbec84bf3d979b438c4cf9ba6e8c73ba5f99f428d"

5. Run the Application
Once your .env file is configured and dependencies are installed, you can run the Flask application:

flask run
# Or if using `if __name__ == '__main__': app.run(...)` directly:
python app.py

The application will typically run on http://127.0.0.1:5000.

Default Admin Credentials
Upon first successful connection to the database, a default admin user will be created if it doesn't already exist:

Username: admin

Password: admin123

Deployment on Render
This application is configured for easy deployment on Render, leveraging its PostgreSQL hosting and web service capabilities.

1. Push to Git Repository
Ensure your project (including app.py, requirements.txt, and Procfile) is pushed to a Git repository (GitHub, GitLab, or Bitbucket).

2. Create a PostgreSQL Database on Render
Log in to your Render Dashboard.

Click New -> PostgreSQL.

Provide a unique name for your database (e.g., my-ticket-app-db).

Select the Region (choose a region close to your users or where you plan to host your web service).

Choose an Instance Type (Free for testing, Pro for production).

Click Create Database.

Once the database is provisioned, navigate to its details page. Crucially, copy the Internal Database URL from the "Connections" section. This URL is used for secure communication between your web service and the database within Render's private network.

3. Create a Web Service on Render
From your Render Dashboard, click New -> Web Service.

Connect to your Git repository where your Flask application code resides.

Name: Give your web service a name (e.g., my-ticket-app).

Region: Select the same region as your PostgreSQL database.

Branch: Choose the Git branch you want to deploy (e.g., main).

Root Directory: Leave empty if your app.py is at the root of your repository.

Runtime: Select Python 3.

Build Command: pip install -r requirements.txt

Start Command: gunicorn app:app (This command tells Render to use Gunicorn to run your Flask app).

Instance Type: Choose "Free" for testing, or a suitable paid plan for production.

4. Add Environment Variables on Render
In the web service creation page, scroll down to the "Environment Variables" section.

Click "Add Environment Variable" for each of the following:

Key: DATABASE_URL

Value: Paste the Internal Database URL you copied from your Render PostgreSQL service.

Key: SECRET_KEY

Value: Generate a strong, random string for your Flask application's secret key. Do NOT use the development key from your .env file here. You can generate one using python -c "import os; print(os.urandom(24).hex())" in your terminal.

5. Deploy
Click Create Web Service.

Render will now automatically build and deploy your application. You can monitor the deployment progress and logs directly from your Render dashboard.

Important Considerations for Render Deployment
File Uploads (Persistent Storage): The uploads directory used for attachments in this application is not persistent across deploys or restarts on Render's standard web services. If you need attachments to persist, you must integrate with an external object storage service like Amazon S3, Google Cloud Storage, or Render's Disk feature (for paid plans).

Database Migrations: For managing database schema changes in a production environment, consider using a tool like Alembic. The current init_db() function uses CREATE TABLE IF NOT EXISTS, which is suitable for initial setup but less robust for evolving schemas.

Database Schema Overview
The application uses the following tables in the PostgreSQL database:

users: Stores user information (id, username, password, role, name, email).

tickets: Stores ticket details (id, title, description, category, priority, status, created_by, created_by_name, assigned_to, assigned_to_name, created_at, updated_at).

comments: Stores comments and activity logs related to tickets (id, ticket_id, user_id, user_name, message, timestamp, type).

attachments: Stores metadata for uploaded files (id, ticket_id, original_name, stored_name, file_path).

notifications: Stores user notifications (id, user_id, message, ticket_id, timestamp, read).

approval_history: Records approval/rejection actions for tickets (id, ticket_id, approver, role, action, timestamp).

Usage and Roles
The application supports distinct user roles, each with specific permissions and functionalities:

User:

Can create new tickets.

Can view their own tickets.

Can add comments to their own tickets.

Receives notifications related to their tickets.

Technician:

Can view tickets assigned to them.

Can update the status of assigned tickets.

Can add comments to assigned tickets.

Receives notifications for new assignments and comments on their assigned tickets.

HOD (Head of Department):

Can view tickets created by users in their department (if applicable, or all tickets if no department logic implemented).

Can approve or reject tickets that are Pending HOD Approval.

Can view users.

IT Head:

Can view all tickets.

Can approve or reject tickets that are Pending IT Head Approval.

Can assign tickets to technicians.

Can view and manage users.

Admin:

Full access to all functionalities.

Can view and manage all tickets.

Can create and manage all user roles.

Can oversee all approval processes.

License
This project is open-source and available under the MIT License.
