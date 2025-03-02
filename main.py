import mysql.connector
from fastapi.responses import JSONResponse
from fastapi import Form
from passlib.context import CryptContext
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import mysql.connector
from mysql.connector import Error
from fastapi.middleware.cors import CORSMiddleware
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.schema import SystemMessage, AIMessage, HumanMessage
from dotenv import load_dotenv
from fastapi import Query

load_dotenv()

app = FastAPI()

# Allow frontend to communicate with backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    conn = mysql.connector.connect(
        host="localhost",
        database="agentica",
        user="root",
        password="password",
        port=8000
    )
    cursor=conn.cursor()
    if conn.is_connected():
        print("Database connection was successful")

except Error as error:
    print("Connecting to database failed")
    print("Error: ", error)


# Job Posting Model
class PostData(BaseModel):
    job_description: str
    hiring_criteria: str
    qualification: str
    company_id: int  # Added company_id

@app.post("/job_posting")
async def receive_post(job: PostData):
    try:
        query = """INSERT INTO job_post (job_description, hiring_criteria, qualification, company_id)
                   VALUES (%s, %s, %s, %s);"""
        cursor.execute(query, (job.job_description, job.hiring_criteria, job.qualification, job.company_id))
        conn.commit()

        cursor.execute("SELECT LAST_INSERT_ID();")
        job_id = cursor.fetchone()[0]
        return {"job_id": job_id, "company_id": job.company_id}

    except Exception as e:
        return {"error": "there was an error"}

@app.get("/candidate_get_jobs")
async def get_jobs():
    if conn is None:
        return JSONResponse(content={"error": "Database connection failed"}, status_code=500)

    cursor.execute("SELECT job_id, job_description, hiring_criteria, qualification FROM job_post ORDER BY created_at ASC;")
    rows = cursor.fetchall()

    jobs_list = [
        {
            "job_id": row[0],
            "job_description": row[1],
            "hiring_criteria": row[2],
            "qualification": row[3]
        }
        for row in rows
    ]

    return JSONResponse(content={"jobs": jobs_list})

@app.post("/job_apply")
async def apply(
    name: str = Form(...),
    education: str = Form(...),
    contact: str = Form(...),
    email: str = Form(...),
    job_id: int = Form(...)
):
    if conn is None:
        return JSONResponse(content={"error": "Database connection failed"}, status_code=500)

    try:
        query = """INSERT INTO job_application (job_id, name, education, contact, email) 
                   VALUES (%s, %s, %s, %s, %s)"""
        values = (job_id, name, education, contact, email)
        cursor.execute(query, values)
        conn.commit()

        # Fetch the last inserted candidate_id
        candidate_id = cursor.lastrowid  # Get auto-incremented candidate_id

        return JSONResponse(content={"message": "Application submitted successfully!", "candidate_id": candidate_id}, status_code=200)
    except mysql.connector.Error as error:
        return JSONResponse(content={"error": str(error)}, status_code=500)

# Password Hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Model for company login
class CompanyLogin(BaseModel):
    company_id: int
    password: str

# Model for job postings
class JobCreate(BaseModel):
    job_description: str
    hiring_criteria: str
    qualification: str
    company_id: int  # Ensuring the job belongs to the logged-in company

# 🔹 Company Login Endpoint
@app.post("/company_login")
def login_company(company: CompanyLogin):
    query = "SELECT password_hash FROM companies WHERE company_id = %s"
    cursor.execute(query, (company.company_id,))
    result = cursor.fetchone()

    if not result:
        raise HTTPException(status_code=401, detail="Invalid company ID")

    stored_password_hash = result[0]
    if not pwd_context.verify(company.password, stored_password_hash):
        raise HTTPException(status_code=401, detail="Invalid password")

    return {"message": "Login successful!", "company_id": company.company_id}

# 🔹 Get Jobs Posted by Logged-in Company
@app.get("/company_jobs/{company_id}")
def get_company_jobs(company_id: int):
    query = "SELECT job_id, job_description, hiring_criteria, qualification FROM job_post WHERE company_id = %s"
    cursor.execute(query, (company_id,))
    jobs = cursor.fetchall()

    job_list = []
    for job in jobs:
        job_list.append({
            "job_id": job[0],
            "job_description": job[1],
            "hiring_criteria": job[2],
            "qualification": job[3]
        })

    return {"jobs": job_list}

# Request Models
class CompanyCreate(BaseModel):
    company_name: str
    password: str

@app.post("/register_company")
def register_company(company: CompanyCreate):
    hashed_password = pwd_context.hash(company.password)

    try:
        query = "INSERT INTO companies (company_name, password_hash) VALUES (%s, %s)"
        cursor.execute(query, (company.company_name, hashed_password))
        conn.commit()

        # Retrieve the company_id of the newly created company
        cursor.execute("SELECT LAST_INSERT_ID()")
        company_id = cursor.fetchone()[0]

        return {"message": "Company registered successfully!", "company_id": company_id}
    except mysql.connector.Error as err:
        conn.rollback()
        return HTTPException(status_code=400, detail=f"Error: {err}")


# AI Model Initialization
chat_model = ChatGoogleGenerativeAI(model="gemini-1.5-flash", temperature=0.7)


class InterviewRequest(BaseModel):
    candidate_id: int
    job_id: int


class InterviewResponse(BaseModel):
    response: str


chat_history=[]




@app.post("/continue_interview/")
def continue_interview(response: InterviewResponse):
    user_response = response.response

    # Add user response to chat history
    chat_history.append(HumanMessage(content=user_response))

    # AI evaluates the response and asks the next question
    ai_message = chat_model.invoke(chat_history)
    chat_history.append(ai_message)  # Store AI's response

    return {"ai_message": ai_message.content.strip()}



@app.get("/get_candidate_info_start_interivew")
def get_candidate_info(job_id: int = Query(...), candidate_id: int = Query(...)):
    if conn is None:
        return {"error": "Database connection failed"}


    try:
        # Fetch job details
        cursor.execute("SELECT * FROM job_post WHERE job_id = %s", (job_id,))
        job_data = cursor.fetchone()

        # Fetch candidate details
        cursor.execute("SELECT * FROM job_application WHERE candidate_id = %s", (candidate_id,))
        candidate_data = cursor.fetchone()

        if not job_data or not candidate_data:
            return {"error": "Job or Candidate not found"}

        # Generate first AI question
        ai_message = chat_model.invoke([HumanMessage(
            content=f"""
You are an AI interviewer conducting a structured job interview. 
Your task is to ask a candidate relevant questions based on the provided job description, qualifications, and hiring criteria. 
Keep the conversation professional and concise. 

you can ask question only of 2 lines max and the ask the questions dynamically based on the job 
here is details 
job detail={job_data}
candidate detail ={candidate_data}

the questions should be as short as possible only one question at a time and of only 1 line question
i emphasis only 1 question you can ask at a time if  you ask more then 1 question at a time then you are an bad chat bot  
and don't forget to greet the candidate first that is important .
""")])

        chat_history.append(ai_message)

        return {"ai_message": ai_message.content.strip()}

    except Error as e:
        return {"error": str(e)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7450)