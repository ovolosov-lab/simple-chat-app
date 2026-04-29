# Colleagues Messenger
![License: MIT](https://shields.io)

Simple messenger: A web application built with FastAPI and PostgreSQL, fully containerized with Docker.

This app is designed for team communication, task management, and sharing project documentation. 
This is an **educational project** developed while learning the FastAPI framework. 

I would be delighted if you'd try this messenger and share your feedback, bug reports, or suggestions for new features!

## ✨ Key Features
- **User Authentication:** Authentication using JWT tokens.
- **Real-time Messaging:** Fast communication between team members, including file sharing.
- **Interactive Reactions:** Support for "likes" and "read" receipts to keep the team engaged.
- **Search:** Quick search through messages.
- **Task Management:** Create, edit and discuss project-related tasks. Observe them on the diagram.
- **File Sharing:** Support for uploading and storing important project documentation.

## 🛠 Technology Stack
- **Language:** Python 3.12 (Slim)
- **Framework:** FastAPI + SQLAlchemy (Async)
- **Database:** PostgreSQL 16
- **Containerization:** Docker & Docker Compose 

## 🚀 Quick Start

To run the project, you need to have [Docker](https://docker.com) installed.

1. **Clone the repository:**
   ```bash
   git clone https://github.com
   cd project-name   ```

2. **Configure Environment Variables:**
   Create a `.env` file based on the provided template:
   ```bash
   cp .env.example .env
   ```
   *Note: Don't forget to fill in your real credentials in the `.env` file!*

3. **Launch with Docker Compose:**
   ```bash
   docker-compose up --build
   ```

The application will be available at: [http://localhost:8000](http://localhost:8000)

## 📖 API Documentation
Once the app is running, you can explore the API here:
- **Swagger UI:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc:** [http://localhost:8000/redoc](http://localhost:8000/redoc)

## ⚙️ Environment Variables
The application requires the following variables (defined in your `.env`):
- `DB_USER` — Database username
- `DB_PASSWORD` — Database password
- `DB_NAME` — Database name
- `DB_HOST` — Database host (use `postgres_container` for Docker)
- `DB_PORT` — Database port (default: 5432)

## 📄 License
This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.
