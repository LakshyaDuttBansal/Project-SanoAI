import uuid
import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
import jwt

# --- SECURITY CONFIGURATION ---
SECRET_KEY = "sano_ai_hackathon_super_secret_jwt_key_change_in_production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

app = FastAPI(title="SanoAI Core Engine API with Authentication", version="3.0.0")

# --- IN-MEMORY DATABASES (Replace with PostgreSQL/SQLAlchemy in production) ---
USERS_DB = {}     # Key: email -> User details + hashed password
SESSIONS_DB = {}  # Key: user_id -> User health records & profile data

# --- HELPER FUNCTIONS FOR SECURITY ---
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.datetime.utcnow() + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials or token expired",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None or user_id not in SESSIONS_DB:
            raise credentials_exception
        return SESSIONS_DB[user_id]
    except jwt.PyJWTError:
        raise credentials_exception

# --- PYDANTIC SCHEMAS ---
class UserRegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str

class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    name: str

class MedicalRecordCreate(BaseModel):
    title: str
    category: str  # Lab Reports, Prescriptions, Imaging, Doctor Notes, Other
    date: str
    status: str    # Normal, Needs Attention, Active, Low

class MedicalRecordResponse(MedicalRecordCreate):
    id: str

class CopilotRequest(BaseModel):
    message: str

class CopilotResponse(BaseModel):
    response: str
    source_document: Optional[str] = None
    disclaimer: str


# ==========================================
# 1. AUTHENTICATION ENDPOINTS (SIGNUP & LOGIN)
# ==========================================

@app.post("/api/auth/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def signup(user_data: UserRegisterRequest):
    """Registers a new user, hashes password, creates a health session, and returns JWT token."""
    if user_data.email.lower() in USERS_DB:
        raise HTTPException(status_code=400, detail="An account with this email already exists.")

    user_id = str(uuid.uuid4())
    hashed_pwd = hash_password(user_data.password)

    # Store user security profile
    USERS_DB[user_data.email.lower()] = {
        "user_id": user_id,
        "name": user_data.name,
        "email": user_data.email.lower(),
        "hashed_password": hashed_pwd
    }

    # Initialize empty health session profile for this user
    SESSIONS_DB[user_id] = {
        "user_id": user_id,
        "profile": {"name": user_data.name, "email": user_data.email.lower()},
        "stats": {
            "health_records_count": 0,
            "new_records_this_month": 0,
            "active_medications": 0,
            "medication_reminders_today": 0,
            "upcoming_appointments": 0,
            "next_appointment_date": None,
            "avg_sleep_hours": "7.0 hrs"
        },
        "records": [],
        "medications": [],
        "wellness": {"steps": 0, "avg_steps": 0, "avg_sleep": "7.0 hrs", "water_intake": "0 L"}
    }

    token = create_access_token({"sub": user_id})
    return {"access_token": token, "token_type": "bearer", "user_id": user_id, "name": user_data.name}


@app.post("/api/auth/login", response_model=TokenResponse)
def login(login_data: UserLoginRequest):
    """Authenticates user credentials and returns JWT token."""
    email_clean = login_data.email.lower()
    user_in_db = USERS_DB.get(email_clean)

    if not user_in_db or not verify_password(login_data.password, user_in_db["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = user_in_db["user_id"]
    token = create_access_token({"sub": user_id})
    return {"access_token": token, "token_type": "bearer", "user_id": user_id, "name": user_in_db["name"]}


# ==========================================
# 2. PROTECTED HEALTH ENGINE ENDPOINTS
# ==========================================

@app.get("/api/overview")
def get_overview_dashboard(current_user: dict = Depends(get_current_user)):
    """Returns dynamic overview data for the authenticated user."""
    name = current_user["profile"]["name"]
    return {
        "greeting": f"Good morning, {name}!",
        "branding": "SanoAI - Your Personal Health Copilot",
        "stats": current_user["stats"],
        "health_insights": [
            {
                "id": str(uuid.uuid4()),
                "title": f"Welcome back, {name}",
                "description": "Upload your medical records to get personalized AI analysis.",
                "type": "info"
            }
        ],
        "wellness_overview": current_user["wellness"]
    }


@app.get("/api/records", response_model=List[MedicalRecordResponse])
def get_medical_records(category: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    """Fetches uploaded records for the currently authenticated user."""
    records = current_user["records"]
    if category and category != "All Records":
        return [r for r in records if r["category"].lower() == category.lower()]
    return records


@app.post("/api/records", response_model=MedicalRecordResponse)
def upload_medical_record(record: MedicalRecordCreate, current_user: dict = Depends(get_current_user)):
    """Creates a new health record for the logged-in user."""
    new_record = {"id": str(uuid.uuid4()), **record.dict()}
    current_user["records"].append(new_record)
    current_user["stats"]["health_records_count"] = len(current_user["records"])
    return new_record


@app.post("/api/ai/copilot", response_model=CopilotResponse)
def sano_copilot_chat(request: CopilotRequest, current_user: dict = Depends(get_current_user)):
    """Processes queries specific to the logged-in user's records."""
    user_name = current_user["profile"]["name"]
    user_records = current_user.get("records", [])

    if not user_records:
        answer = f"Hello {user_name}! You haven't uploaded any medical records yet. Upload a report so SanoAI can analyze it for you."
        source = None
    else:
        matching_record = next(
            (r for r in user_records if any(term in r["title"].lower() for term in request.message.lower().split())), 
            None
        )
        if matching_record:
            answer = f"I examined your record '{matching_record['title']}' ({matching_record['date']}). Status: {matching_record['status']}."
            source = f"{matching_record['title']} - {matching_record['date']}.pdf"
        else:
            answer = f"I've analyzed your question, {user_name}. SanoAI is ready to assist across your {len(user_records)} uploaded records."
            source = None

    return {
        "response": answer,
        "source_document": source,
        "disclaimer": "This information is for educational purposes only and not a medical diagnosis. Consult your healthcare provider."
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)