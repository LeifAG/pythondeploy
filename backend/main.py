from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
import subprocess
import os
import shutil
import pymysql

app = FastAPI()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/token")
STUDENT_BASE_PATH = "/home/ftpuser/"
PYTHON_PROJECT_FOLDER = "python"
PORT_BASE = 8000  # Base port for student projects

# Database credentials
DB_HOST = "localhost"
DB_USER = "ftpuser"
DB_PASSWORD = "Abcd1234"
DB_NAME = "pureftpd"

# Authenticate user against Pure-FTPd MySQL database
def authenticate_user(username: str, password: str):
    try:
        conn = pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD, database=DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT Password FROM users WHERE User=%s", (username,))
        result = cursor.fetchone()
        conn.close()
        
        if result and result[0] == password:
            return True
    except Exception as e:
        print("Database error:", str(e))
    return False

@app.post("/token")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    if not authenticate_user(form_data.username, form_data.password):
        raise HTTPException(status_code=400, detail="Invalid credentials")
    return {"access_token": form_data.username, "token_type": "bearer"}

def get_available_port(student: str):
    # This function should return a unique port for each student
    # For simplicity, we use a base port + student index
    # In a real scenario, you might want to check if the port is actually available
    student_index = sum(ord(char) for char in student)  # Simple hash function
    return PORT_BASE + (student_index % 1000)  # Ensure port is within a range

@app.get("/status/{student}")
def get_status(student: str, token: str = Depends(oauth2_scheme)):
    service_name = f"student_project@{student}"
    try:
        result = subprocess.run(["systemctl", "is-active", service_name], capture_output=True, text=True)
        status = "running" if result.stdout.strip() == "active" else "stopped"
        return {"student": student, "status": status}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/restart/{student}")
def restart_app(student: str, token: str = Depends(oauth2_scheme)):
    service_name = f"student_project@{student}"
    try:
        subprocess.run(["systemctl", "restart", service_name], check=True)
        return {"status": "restarted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/deploy/{student}")
def deploy_project(student: str, repo_url: str, framework: str, token: str = Depends(oauth2_scheme)):
    student_path = os.path.join(STUDENT_BASE_PATH, student)
    project_path = os.path.join(student_path, PYTHON_PROJECT_FOLDER)
    venv_path = os.path.join(student_path, "venv", "bin", "python")
    port = get_available_port(student)
    
    # Remove existing project if it exists
    if os.path.exists(project_path):
        shutil.rmtree(project_path)
    
    os.makedirs(project_path, exist_ok=True)
    
    try:
        subprocess.run(["git", "clone", repo_url, project_path], check=True)
        subprocess.run([venv_path, "-m", "pip", "install", "-r", os.path.join(project_path, "requirements.txt")], check=True)
        
        if framework == "django":
            subprocess.run([venv_path, "manage.py", "migrate"], cwd=project_path, check=True)
            subprocess.run([venv_path, "manage.py", "collectstatic", "--noinput"], cwd=project_path, check=True)
        
        # Create systemd service file
        service_file = f"/etc/systemd/system/student_project@{student}.service"
        with open(service_file, "w") as f:
            f.write(f"""
[Unit]
Description=Student Python Project {student}
After=network.target

[Service]
User=www-data
WorkingDirectory={project_path}
ExecStart={venv_path} -m uvicorn app:app --host 0.0.0.0 --port={port}
Restart=always
RestartSec=3
KillMode=process

[Install]
WantedBy=multi-user.target
            """)
        
        # Reload systemd and enable service
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "enable", f"student_project@{student}"], check=True)
        subprocess.run(["systemctl", "start", f"student_project@{student}"], check=True)

        return {"status": "deployed", "url": f"http://www.its.ax/~{student}/python"}
    
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Deployment failed: {str(e)}")

@app.delete("/delete/{student}")
def delete_project(student: str, token: str = Depends(oauth2_scheme)):
    project_path = os.path.join(STUDENT_BASE_PATH, student, PYTHON_PROJECT_FOLDER)
    service_file = f"/etc/systemd/system/student_project@{student}.service"
    
    try:
        # Stop and disable systemd service
        subprocess.run(["systemctl", "stop", f"student_project@{student}"], check=False)
        subprocess.run(["systemctl", "disable", f"student_project@{student}"], check=False)
        
        # Remove systemd service file
        if os.path.exists(service_file):
            os.remove(service_file)
        
        # Reload systemd
        subprocess.run(["systemctl", "daemon-reload"], check=False)
        
        # Delete project files
        if os.path.exists(project_path):
            shutil.rmtree(project_path)
        
        return {"status": "deleted"}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Deletion failed: {str(e)}")
