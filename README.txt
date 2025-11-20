

# AI Job Application Tracker 

A system that reads job-related emails, extracts key information using Python and NLP, and stores the results in PostgreSQL. A Spring Boot backend exposes REST APIs, and a simple HTML/Bootstrap UI can be added.

## Features
* Reads inbox using IMAP
* Extracts company, role, date, and status using Hugging Face NLP
* Stores structured data in PostgreSQL
* Provides REST APIs via Spring Boot

## Tech Stack

Python, Transformers, PyTorch, PostgreSQL, Spring Boot, HTML, Bootstrap, JavaScript

## Project Structure

emailExtractor/       Python email reader + NLP
jobtracker-backend/   Spring Boot API
frontend/             Optional web UI

## Python Setup

Install:
pip install torch transformers psycopg2
Run:
python main.py
## Spring Boot Setup
Start backend:
cd jobtracker-backend
mvn spring-boot:run

API endpoints:


GET /api/applications
POST /api/applications


## Frontend Structure

frontend/
  index.html
  style.css
  app.js

