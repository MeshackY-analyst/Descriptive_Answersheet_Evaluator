################################################################################
################################################################################
## Importing Libraries
################################################################################
################################################################################

import streamlit as st
import tempfile
import os
import io
import sqlite3
import pandas as pd
import numpy as np
import re
import json
import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import textwrap

import agent as agent_module

################################################################################
################################################################################
## Question Count Inference
################################################################################
################################################################################

def infer_question_count(text: str) -> int:
    q1 = len(re.findall(r'\bQ\d+\b', text or "", re.IGNORECASE))
    q2 = len(re.findall(r'\bQuestion\s+\d+\b', text or "", re.IGNORECASE))
    return max(q1, q2, 0)

################################################################################
################################################################################
## Maximum Marks Parsing
################################################################################
################################################################################

def parse_max_marks(resp_text: str, ai_total: float) -> int:
    if not resp_text:
        return 0
    patterns = [
        r"Out of[:\s]*([0-9]+(?:\.[0-9]+)?)",
        r"Maximum\s*Marks[:\s]*([0-9]+(?:\.[0-9]+)?)",
        r"Total\s*marks\s*(possible|out of)[:\s]*([0-9]+(?:\.[0-9]+)?)",
        r"Total\s*possible[:\s]*([0-9]+(?:\.[0-9]+)?)",
        r"Max[:\s]*([0-9]+(?:\.[0-9]+)?)",
        r"out of\s*([0-9]{1,4})"
    ]
    for pat in patterns:
        m = re.search(pat, resp_text, re.IGNORECASE)
        if m:
            for g in m.groups():
                if g:
                    try:
                        return int(float(g))
                    except:
                        continue
    q_count = infer_question_count(resp_text)
    if q_count > 0:
        return q_count * 7
    try:
        at = float(ai_total)
        if at.is_integer() and at > 0 and at < 2000:
            return int(at)
    except:
        pass
    return 0

################################################################################
################################################################################
## Marks and Percentage Computation
################################################################################
################################################################################

def compute_marks_and_pct(ai_total_raw, resp_text):
    """
    Simplified - trusts agent.py completely, no normalization
    """
    try:
        ai_raw = float(ai_total_raw)
    except:
        ai_raw = 0.0
    
    max_marks = 0
    patterns = [
        r"Maximum\s*Marks[:\s]*([0-9]+)",
        r"Out of[:\s]*([0-9]+)",
        r"Total\s*possible[:\s]*([0-9]+)"
    ]
    
    for pat in patterns:
        m = re.search(pat, resp_text, re.IGNORECASE)
        if m:
            try:
                max_marks = int(m.group(1))
                break
            except:
                continue
    
    if max_marks == 0 and ai_raw > 0:
        if ai_raw <= 100:
            max_marks = 100
        else:
            max_marks = int((ai_raw + 9) // 10 * 10)
    
    shown_marks = round(ai_raw, 2)
    
    # Calculate percentage
    pct = None
    if max_marks and max_marks > 0:
        pct = round((shown_marks / max_marks) * 100, 2)
    
    return {
        "ai_raw": round(ai_raw, 2),
        "inferred_max": max_marks,
        "shown_marks": shown_marks,
        "pct": pct,
        "method": "trusted_agent"
    }

################################################################################
################################################################################
## Database Configuration
################################################################################
################################################################################

DB_FILE = "school_frontend.db"

################################################################################
################################################################################
## Database Initialization
################################################################################
################################################################################

def init_database():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
    CREATE TABLE IF NOT EXISTS teachers (
        teacher_id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        full_name TEXT NOT NULL,
        email TEXT,
        subject TEXT
    )
    ''')
    c.execute('''
    CREATE TABLE IF NOT EXISTS students (
        student_id TEXT PRIMARY KEY,
        full_name TEXT NOT NULL,
        email TEXT,
        course TEXT,
        semester INTEGER
    )
    ''')
    c.execute('''
    CREATE TABLE IF NOT EXISTS evaluations (
        evaluation_id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT NOT NULL,
        teacher_id INTEGER NOT NULL,
        subject TEXT NOT NULL,
        total_marks INTEGER NOT NULL,
        max_marks INTEGER NOT NULL,
        ai_evaluation_text TEXT,
        evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    conn.commit()
    conn.close()

################################################################################
################################################################################
## Insert Sample Data
################################################################################
################################################################################

def add_sample_data():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    teachers = [
        ('teacher1', 'John Doe', 'john@school.com', 'Data Analytics'),
        ('teacher2', 'Jane Smith', 'jane@school.com', 'Machine Learning'),
    ]
    for t in teachers:
        try:
            c.execute('INSERT INTO teachers (username, full_name, email, subject) VALUES (?, ?, ?, ?)', t)
        except sqlite3.IntegrityError:
            pass
    students = [
        ('STU001', 'Alice Johnson', 'alice@student.com', 'Computer Science', 3),
        ('STU002', 'Bob Williams', 'bob@student.com', 'Computer Science', 3),
        ('STU003', 'Charlie Brown', 'charlie@student.com', 'Data Science', 5),
        ('STU004', 'Diana Prince', 'diana@student.com', 'AI/ML', 7),
        ('STU005', 'Ethan Hunt', 'zaggu2004@gmail.com', 'Computer Science', 5),
    ]
    for s in students:
        try:
            c.execute('INSERT INTO students (student_id, full_name, email, course, semester) VALUES (?, ?, ?, ?, ?)', s)
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()

################################################################################
################################################################################
## Retrieve All Students
################################################################################
################################################################################

def get_all_students():
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT * FROM students ORDER BY student_id", conn)
    conn.close()
    return df

################################################################################
################################################################################
## Fetch Student By ID
################################################################################
################################################################################

def get_student_by_id(student_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM students WHERE student_id = ?", (student_id,))
    s = c.fetchone()
    conn.close()
    return s

################################################################################
################################################################################
## Teacher ID Retrieval
################################################################################
################################################################################

def get_teacher_id(username):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT teacher_id FROM teachers WHERE username = ?", (username,))
    r = c.fetchone()
    conn.close()
    return r[0] if r else None

################################################################################
################################################################################
## Save evaluation to database
################################################################################
################################################################################

def save_evaluation_to_db(student_id, teacher_id, subject, marks, max_marks, ai_text):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        INSERT INTO evaluations (student_id, teacher_id, subject, total_marks, max_marks, ai_evaluation_text)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (student_id, teacher_id, subject, marks, max_marks, ai_text))
    conn.commit()
    conn.close()

################################################################################
################################################################################
## Student Evaluation History
################################################################################
################################################################################

def get_student_history(student_id):
    conn = sqlite3.connect(DB_FILE)
    query = '''
        SELECT e.evaluation_id, e.subject, e.total_marks, e.max_marks, e.evaluated_at, t.full_name as teacher_name
        FROM evaluations e JOIN teachers t ON e.teacher_id = t.teacher_id
        WHERE e.student_id = ?
        ORDER BY e.evaluated_at DESC
    '''
    df = pd.read_sql_query(query, conn, params=(student_id,))
    conn.close()
    return df

################################################################################
################################################################################
## Evaluation Details Retrieval
################################################################################
################################################################################

def get_evaluation_details(evaluation_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT ai_evaluation_text FROM evaluations WHERE evaluation_id = ?", (evaluation_id,))
    r = c.fetchone()
    conn.close()
    return r[0] if r else None

################################################################################
################################################################################
## Evaluation Email Sender
################################################################################
################################################################################

def send_evaluation_email(to_email, student_name, student_id, subject, marks, feedback, eval_text):
    sender = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_PASSWORD")
    if not sender or not password:
        return False, "Email not configured. Set EMAIL_SENDER and EMAIL_PASSWORD environment variables"

    try:
        email_subject = f"Your {subject} Evaluation Result"
        body = f"""Hello {student_name},

Your paper for the subject "{subject}" has been evaluated.

Marks obtained: {marks}
Feedback: {feedback}

Regards,
Academic Evaluation System
"""
        body = textwrap.dedent(body).strip()
        # create eval file
        file_name = f"{student_id}_feedback.txt"
        with open(file_name, "w", encoding="utf-8") as f:
            f.write(eval_text or "No evaluation text provided.")

        msg = MIMEMultipart()
        msg["From"] = sender
        msg["To"] = to_email
        msg["Subject"] = email_subject
        msg.attach(MIMEText(body, "plain"))

        with open(file_name, "rb") as attachment:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={os.path.basename(file_name)}")
            msg.attach(part)

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(sender, password)
            server.send_message(msg)

        os.remove(file_name)
        return True, "Email sent successfully."
    except smtplib.SMTPAuthenticationError:
        return False, "Email authentication failed. Check credentials/app password."
    except Exception as e:
        return False, f"Email failed: {e}"

################################################################################
################################################################################
## Workflow Invocation Handler
################################################################################
################################################################################

def call_workflow_invoke(payload: dict):
    try:
        wf = getattr(agent_module, "streamlit_workflow", None)
        if wf and hasattr(wf, "invoke"):
            return wf.invoke(payload)
    except Exception as e:
        st.warning(f"workflow.invoke failed: {e}")

    try:
        state = payload.copy()
        if hasattr(agent_module, "pdf_to_image"):
            state = agent_module.pdf_to_image(state)
        if hasattr(agent_module, "llm_evaluator"):
            return agent_module.llm_evaluator(state)

        if hasattr(agent_module, "workflow") and callable(agent_module.workflow):
            return agent_module.workflow(payload)
    except Exception as e:
        raise e
    raise RuntimeError("No suitable invoke method found in agent.py")

################################################################################
################################################################################
## Persist Marks via Agent Tool
################################################################################
################################################################################

def persist_marks_via_agent(file_path, student_id, subject, marks):
    try:
        func = getattr(agent_module, "update_marks", None)
        if func is None:
            return False, "agent.update_marks not found"
        if hasattr(func, "invoke"):
            res = func.invoke({
                "file_path": file_path,
                "student_id": student_id,
                "subject": subject,
                "marks": int(marks)
            })
            return True, str(res)
        else:
            res = func(file_path, student_id, subject, int(marks))
            return True, str(res)
    except Exception as e:
        return False, str(e)

if not os.path.exists(DB_FILE):
    init_database()
    add_sample_data()

################################################################################
################################################################################
## Customizing The Webpage
################################################################################
################################################################################

st.set_page_config(layout="wide")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

* { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }

#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}

.main { background: linear-gradient(135deg, #f5f7fa 0%, #e8edf2 100%); }

h1 {
    color: white;
    font-weight: 700;
    font-size: 2.5rem;
    padding-bottom: 1rem;
    border-bottom: 3px solid #4f46e5;
    margin-bottom: 2rem;
}

.stTabs [data-baseweb="tab-highlight"] {
    display: none !important;
}

.stTabs [aria-selected="true"] {
    background: transparent;  /* No background box */
    color: #6366f1;  /* Purple text instead */
    box-shadow: none;
    border-bottom: 3px solid #6366f1;  /* Underline instead */
}

.stButton > button {
    background: linear-gradient(135deg, #4f46e5 0%, #6366f1 100%);
    color: #1a1a2e;
    border-radius: 8px;
    padding: 0.6rem 1.5rem;
    font-weight: 600;
    box-shadow: 0 4px 12px rgba(79, 70, 229, 0.2);
}

.stButton > button:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 20px rgba(79, 70, 229, 0.4);
}

div[data-testid="stMetricValue"] {
    font-size: 2rem;
    font-weight: 700;
    color: #4f46e5;
}

.stFileUploader {
    background: #1a1a2e;
    border: 2px dashed #cbd5e1;
    border-radius: 12px;
    padding: 2rem;
}

.stSuccess {
    background: linear-gradient(135deg, #10b981 0%, #059669 100%);
    color: #1a1a2e;
    border-radius: 8px;
    padding: 1rem;
}

/* Input fields - dark theme matching */
.stTextInput > div > div > input,
.stSelectbox > div > div > select,
.stNumberInput > div > div > input {
    border: 2px solid #3a3a3a !important;
    background-color: #2d2d2d !important;
    color: white !important;
}

/* Remove blue focus border */
.stTextInput > div > div > input:focus,
.stSelectbox > div > div > select:focus,
.stNumberInput > div > div > input:focus {
    border-color: #6366f1 !important;
    box-shadow: none !important;
    outline: none !important;
}

/* Dropdown options background */
.stSelectbox div[role="listbox"] {
    background-color: #2d2d2d !important;
}

</style>
""", unsafe_allow_html=True)


st.title("Answer Evaluation System Using Agentic AI")

if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'username' not in st.session_state:
    st.session_state.username = None
if 'name' not in st.session_state:
    st.session_state.name = None

USERS = {
    'teacher1': {'password': 'password1', 'name': 'John Doe'},
    'teacher2': {'password': 'password1', 'name': 'Jane Smith'}
}

def check_login(username, password):
    if username in USERS and USERS[username]['password'] == password:
        return True, USERS[username]['name']
    return False, None

if not st.session_state.logged_in:
    st.header("Teacher Login")
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submit = st.form_submit_button("Login")
        if submit:
            ok, nm = check_login(username, password)
            if ok:
                st.session_state.logged_in = True
                st.session_state.username = username
                st.session_state.name = nm
                st.rerun()
                
            else:
                st.error("Invalid credentials")
    st.stop()

with st.sidebar:
    st.markdown(f"**Logged in as:** {st.session_state.name} ({st.session_state.username})")
    tid = get_teacher_id(st.session_state.username)
    if tid:
        st.write(f"Teacher ID: {tid}")
    if st.button("Logout"):
        st.session_state.clear()
        st.rerun()

################################################################################
################################################################################
## Tabs
################################################################################
################################################################################

tab1, tab2, tab3, tab4, tab5 = st.tabs(["Students", "Evaluate", "Review", "History", "Help"])

################################################################################
################################################################################
## Students
################################################################################
################################################################################

with tab1:
    st.header("All Students")
    students_df = get_all_students()
    search = st.text_input("Search students")
    if search:
        students_df = students_df[students_df['full_name'].str.contains(search, case=False) | students_df['student_id'].str.contains(search, case=False)]
    st.dataframe(students_df, use_container_width=True)

################################################################################
################################################################################
## Evaluate
################################################################################
################################################################################

with tab2:
    st.header("Upload & Evaluate (calls agent.py)")
    students_df = get_all_students()
    options = {f"{r['student_id']} - {r['full_name']}": r['student_id'] for _, r in students_df.iterrows()}
    selected_display = st.selectbox("Select Student", list(options.keys()))
    selected_student_id = options[selected_display]
    student = get_student_by_id(selected_student_id)
    if student:
        st.write(f"Name: {student[1]} | Email: {student[2]}")

    subject = st.text_input("Subject", value="Data Analytics")
    uploaded_file = st.file_uploader("Upload answer sheet (PDF)", type=["pdf"])
    if uploaded_file:
        st.success(f"Selected: {uploaded_file.name}")
        if st.button("Start Evaluation"):
            tmp_path = os.path.join(tempfile.gettempdir(), f"temp_{uploaded_file.name}")
            with open(tmp_path, "wb") as f:
                f.write(uploaded_file.getbuffer())

            with st.spinner("AI evaluating (calling agent.py)..."):
                try:
                    payload = {
                        "ans_sheet_path": tmp_path,
                        "Studentid": selected_student_id,
                        "subject": subject
                    }
                    result = call_workflow_invoke(payload)
                    resp_text = result.get("resp", "") if isinstance(result, dict) else str(result)
                    total_marks = result.get("total_marks", 0) if isinstance(result, dict) else 0
                    modified_marks = result.get("modified_marks", total_marks) if isinstance(result, dict) else total_marks

                    st.session_state.evaluation_result = resp_text
                    st.session_state.ai_marks = total_marks
                    st.session_state.optimized_marks = modified_marks
                    st.session_state.student_id = selected_student_id
                    st.session_state.student_name = student[1]
                    st.session_state.subject = subject
                    st.session_state.student_email = student[2]
                    st.session_state.evaluation_done = True
                    st.session_state.ans_sheet_path = tmp_path  
                    st.session_state.retry_count = 0 

                    st.success("Evaluation complete. Go to Review tab.")
                except Exception as e:
                    st.error(f"Evaluation failed: {e}")
                    # Only delete on error
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except:
                        pass
################################################################################
################################################################################
## Review
################################################################################
################################################################################

with tab3:
    st.header("Review & Approve")
    if st.session_state.get("evaluation_done"):
        resp_text = st.session_state.evaluation_result
        ai_raw = st.session_state.ai_marks
        optimized = st.session_state.optimized_marks

        computed = compute_marks_and_pct(ai_raw, resp_text)
        ai_raw_disp = computed["ai_raw"]
        shown_marks = computed["shown_marks"]
        max_marks = computed["inferred_max"]
        pct = computed["pct"]
        method = computed["method"]

        col1, col2, col3 = st.columns(3)
        col1.metric("Marks obtained", ai_raw_disp)
        col2.metric("Out of", max_marks)
        col3.metric("Percentage", f"{pct}%" if pct is not None else "N/A")

        if method != "no_change":
            st.warning(f"Frontend adjusted AI score automatically: {method}")

        with st.expander("Full AI evaluation (raw):", expanded=True):
            st.code(resp_text[:100000])

        st.markdown("### Teacher actions")
        colA, colB, colC, colD = st.columns(4)
        with colA:
            if st.button("Approve & Save"):
                tid = get_teacher_id(st.session_state.username)
                final_marks = shown_marks
                if hasattr(agent_module, "evaluation_optimizer"):
                    try:
                        agent_module.evaluation_optimizer(ai_raw_disp, final_marks, mode="update")
                    except Exception as e:
                        st.warning(f"Could not update agent optimizer: {e}")
                save_evaluation_to_db(st.session_state.student_id, tid, st.session_state.subject, int(final_marks), max_marks or 0, resp_text)

                ok, msg = persist_marks_via_agent("student_marks.xlsx", st.session_state.student_id, st.session_state.subject, int(final_marks))
                if ok:
                    st.success(f"Saved to DB & Excel. {msg}")
                else:
                    st.warning(f"Saved to DB but Excel update failed: {msg}")

                ################################################################################
                ################################################################################
                ## Cleanup temp file after approval
                ################################################################################
                ################################################################################

                try:
                    temp_path = st.session_state.get("ans_sheet_path", "")
                    if temp_path and os.path.exists(temp_path):
                        os.remove(temp_path)
                except:
                    pass
                
                st.rerun()

        with colB:
            if st.button("Reject"):
                st.session_state.show_reject = True

        with colC:
            if st.button("Modify Marks"):
                st.session_state.show_modify = True

        with colD:
            if st.button("Send Email"):
                if not st.session_state.student_email:
                    st.error("Student has no email configured.")
                else:
                    with st.spinner("Sending email..."):
                        success, message = send_evaluation_email(
                            st.session_state.student_email,
                            st.session_state.student_name,
                            st.session_state.student_id,
                            st.session_state.subject,
                            shown_marks,
                            "Approved by teacher",
                            resp_text
                        )
                        if success:
                            st.success(message)
                        else:
                            st.error(message)

        if st.session_state.get("show_modify", False):
            st.info("Modify marks and press Save")
            new_marks = st.number_input("Enter new marks", min_value=0, max_value=10000, value=int(shown_marks))
            colx, coly = st.columns(2)
            with colx:
                if st.button("Save Modified"):
                    st.session_state.optimized_marks = new_marks
                    st.session_state.show_modify = False
                    st.success(f"Updated to {new_marks}")
                    st.rerun()
            with coly:
                if st.button("Cancel"):
                    st.session_state.show_modify = False
                    st.rerun()

        ################################################################################
        ################################################################################
        ## Reject dialog for reevaluation
        ################################################################################
        ################################################################################
        
        if st.session_state.get("show_reject", False):
            st.warning("⚠️ Reject and request re-evaluation")
            feedback = st.text_area("Enter feedback for re-evaluation", 
                                   placeholder="Explain what needs to be corrected in the evaluation...",
                                   height=100)
            col_save, col_cancel = st.columns(2)
            with col_save:
                if st.button("Submit Rejection"):
                    if not feedback.strip():
                        st.error("Please provide feedback for re-evaluation")
                    else:
                        retry_count = st.session_state.get("retry_count", 0)
                        if retry_count >= 3:
                            st.error("Maximum re-evaluation attempts (3) reached. Please modify marks manually or cancel.")
                        else:

                            ################################################################################
                            ################################################################################
                            ## Trigger reevaluation with feedback
                            ################################################################################
                            ################################################################################
                            
                            with st.spinner("Re-evaluating with your feedback..."):
                                try:
                                    ################################################################################
                                    ################################################################################
                                    ## Get the original answer sheet path from session if available
                                    ################################################################################
                                    ################################################################################
                                    ans_sheet_path = st.session_state.get("ans_sheet_path", "")
                                    
                                    if not ans_sheet_path:
                                        st.error("Cannot re-evaluate: Original answer sheet path not found. Please re-upload the document.")
                                    else:
                                        payload = {
                                            "ans_sheet_path": ans_sheet_path,
                                            "Studentid": st.session_state.student_id,
                                            "subject": st.session_state.subject,
                                            "teacher_feedback": feedback,
                                            "retry_count": retry_count,
                                            "needs_reevaluation": True
                                        }
                                        result = call_workflow_invoke(payload)
                                        resp_text = result.get("resp", "") if isinstance(result, dict) else str(result)
                                        total_marks = result.get("total_marks", 0) if isinstance(result, dict) else 0
                                        modified_marks = result.get("modified_marks", total_marks) if isinstance(result, dict) else total_marks
                                        
                                        ################################################################################
                                        ################################################################################
                                        ## Update session state with new evaluation
                                        ################################################################################
                                        ################################################################################

                                        st.session_state.evaluation_result = resp_text
                                        st.session_state.ai_marks = total_marks
                                        st.session_state.optimized_marks = modified_marks
                                        st.session_state.retry_count = retry_count + 1
                                        st.session_state.show_reject = False
                                        
                                        st.success(f"Re-evaluation complete (Attempt #{retry_count + 2}). Review updated results above.")
                                        st.rerun()
                                except Exception as e:
                                    st.error(f"Re-evaluation failed: {e}")
                                    st.info("Try modifying marks manually or contact support.")
            with col_cancel:
                if st.button("Cancel Rejection"):
                    st.session_state.show_reject = False
                    st.rerun()
        

        st.markdown("---")
        if st.button("🗑️ Cancel Evaluation (Discard Without Saving)", type="secondary"):
         
            ################################################################################
            ################################################################################
            ## Cleanup temp file
            ################################################################################
            ################################################################################
            try:
                temp_path = st.session_state.get("ans_sheet_path", "")
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)
            except:
                pass
            
            ################################################################################
            ################################################################################
            # Clear evaluation session state
            ################################################################################
            ################################################################################
            
            st.session_state.evaluation_done = False
            st.session_state.evaluation_result = ""
            st.session_state.ai_marks = 0
            st.session_state.optimized_marks = 0
            st.session_state.retry_count = 0
            st.session_state.ans_sheet_path = ""
            st.session_state.show_modify = False
            st.session_state.show_reject = False
            
            st.info("Evaluation cancelled. No data saved.")
            st.rerun()
    else:
        st.info("Please evaluate a student in the Evaluate tab first.")

################################################################################
################################################################################
## History
################################################################################
################################################################################

with tab4:
    st.header("Student History")
    df_students = get_all_students()
    options = {f"{r['student_id']} - {r['full_name']}": r['student_id'] for _, r in df_students.iterrows()}
    sel = st.selectbox("Choose student", list(options.keys()))
    sid = options[sel]
    hist = get_student_history(sid)
    if len(hist) == 0:
        st.info("No history found.")
    else:
        st.dataframe(hist[['subject', 'total_marks', 'max_marks', 'teacher_name', 'evaluated_at']], use_container_width=True)
        sel_eval = st.selectbox("Select evaluation", [f"{r['evaluated_at']} - {r['subject']}" for _, r in hist.iterrows()])
        if st.button("View details"):
            idx = list(hist.index)[ [f"{hist.loc[i,'evaluated_at']} - {hist.loc[i,'subject']}" for i in hist.index].index(sel_eval) ]
            eval_id = hist.loc[idx, 'evaluation_id'].values[0] if 'evaluation_id' in hist.columns else None
            if eval_id:
                text = get_evaluation_details(eval_id)
                if text:
                    st.code(text)
                else:
                    st.info("No detailed text saved for this record.")
            else:
                st.error("Could not find evaluation id.")
                
################################################################################
################################################################################
## Help
################################################################################
################################################################################
with tab5:
    st.header("Credentials for Demo")
    st.markdown("### Demo credentials:\n- teacher1 / password1\n- teacher2 / password1")

st.markdown("<div style='text-align:center; color:gray;'>Answer Evaluation System Using Agentic AI</div>", unsafe_allow_html=True)
# ============================================================================================================================================================== #
# ============================================================================================================================================================== #
# ============================================================================================================================================================== #

