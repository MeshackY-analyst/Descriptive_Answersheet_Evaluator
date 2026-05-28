################################################################################
################################################################################
## Importing Libraries
################################################################################
################################################################################

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from typing import TypedDict, Annotated, List
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import tool
import pandas as pd
import fitz
from PIL import Image
import io
import base64
import re
import json
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import textwrap
import datetime
from dotenv import load_dotenv
load_dotenv()

################################################################################
################################################################################
## Optimizer (Persistent Learning with JSON)
################################################################################
################################################################################

OPTIMIZER_FILE = "optimizer_memory.json"


################################################################################
################################################################################
## Loading the optimizer file
################################################################################
################################################################################

def load_optimizer():
    """Load optimizer parameters from JSON if available."""
    if os.path.exists(OPTIMIZER_FILE):
        try:
            with open(OPTIMIZER_FILE, "r") as f:
                data = json.load(f)
                print(f"Loaded optimizer memory: {data}")
                return data
        except Exception:
            pass
    return {"weight": 1.0, "bias": 0.0, "lr": 0.05, "alpha": 0.3}

################################################################################
################################################################################
## Saving the optimizer file
################################################################################
################################################################################

def save_optimizer(data):
    """Save optimizer parameters to JSON."""
    try:
        with open(OPTIMIZER_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Failed to save optimizer memory: {e}")


optimizer = load_optimizer()

################################################################################
################################################################################
## Using the optimizer for evaluation
################################################################################
################################################################################

def evaluation_optimizer(ai_marks: float, teacher_marks: float = None, mode: str = "inference") -> float:
    """
    Adjusts or updates marks based on learned bias and weight.
    - mode='inference': returns adjusted marks using current bias.
    - mode='update': updates bias/weight based on teacher feedback and saves to JSON.
    """
    current_optimizer = load_optimizer()
    w, b = current_optimizer["weight"], current_optimizer["bias"]

    ################################################################################
    ################################################################################
    # Apply bias & weight
    ################################################################################
    ################################################################################

    if mode == "inference":
        adjusted = (ai_marks * w) + b
        adjusted = round(max(0, adjusted), 2)
        print(f"Adjusted Marks → Raw: {ai_marks}, Adjusted: {adjusted}")
        return adjusted
    
    ################################################################################
    ################################################################################
    # Learning from teacher feedback
    ################################################################################
    ################################################################################

    elif mode == "update" and teacher_marks is not None:
        error = teacher_marks - ai_marks  # teacher correction
        new_w = w + current_optimizer["lr"] * error * 0.01
        new_b = b + current_optimizer["lr"] * error

        ################################################################################
        ################################################################################
        # Exponential smoothing (EWMA)
        ################################################################################
        ################################################################################
        
        current_optimizer["weight"] = current_optimizer["alpha"] * new_w + (1 - current_optimizer["alpha"]) * w
        current_optimizer["bias"] = current_optimizer["alpha"] * new_b + (1 - current_optimizer["alpha"]) * b

        print(f"Optimizer Updated → Δ={error:.2f}, Weight={current_optimizer['weight']:.3f}, Bias={current_optimizer['bias']:.3f}")
        
        ################################################################################
        ################################################################################
        # Save updates persistently
        ################################################################################
        ################################################################################
        
        save_optimizer(current_optimizer)
        return teacher_marks


################################################################################
################################################################################
## LLM Initialization (Gemini Model)
################################################################################
################################################################################
llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key=os.getenv("GEMINI_API_KEY"),
    temperature=0,

)

################################################################################
################################################################################
## Tool: Update Student Marks
################################################################################
################################################################################
@tool
def update_marks(file_path: str, student_id: str, subject: str, marks: int) -> str:
    """Update marks for a student in an Excel file."""
    print(f"\nSaving: {student_id} | {subject} | {marks} marks")
    try:
        try:
            df = pd.read_excel(file_path)
        except FileNotFoundError:
            df = pd.DataFrame(columns=["ID"])
    
        if "ID" not in df.columns:
            df.insert(0, "ID", [])

        if subject not in df.columns:
            df[subject] = None

        if student_id in df["ID"].values:
            idx = df.index[df["ID"] == student_id][0]
            df.at[idx, subject] = marks
        else:
            new_row = {col: None for col in df.columns}
            new_row["ID"] = student_id
            new_row[subject] = marks
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

        df.to_excel(file_path, index=False)
        return f"✓ Marks updated successfully for {student_id}"
    except Exception as e:
        return f"Error: {e}"


################################################################################
################################################################################
## Langgraph State Definition
################################################################################
################################################################################

class State(TypedDict):
    Studentid: str
    subject: str
    ans_sheet_path: str
    ans_sheet: List[Image.Image]
    messages: Annotated[list, add_messages]
    resp: str
    total_marks: int
    teacher_approved: bool
    teacher_feedback: str
    modified_marks: int
    retry_count: int
    needs_reevaluation: bool


graph = StateGraph(State)


def take_info(state: State):
    """Initialize state with defaults only if values not provided."""
    return {
        "Studentid": state.get("Studentid", "mk"),
        "subject": state.get("subject", "Data Analytics"),
        "ans_sheet_path": state.get("ans_sheet_path", "Data Analytics Assignment .pdf"),
        "teacher_approved": state.get("teacher_approved", False),
        "retry_count": state.get("retry_count", 0),
        "needs_reevaluation": state.get("needs_reevaluation", False)
    }


################################################################################
################################################################################
## Answer Sheet Processing (PDF to Images)
################################################################################
################################################################################

def pdf_to_image(state: State):
    doc = fitz.open(state["ans_sheet_path"])
    images = []
    for page in doc:
        zoom_x = 2.0 
        zoom_y = 2.0
        mat = fitz.Matrix(zoom_x, zoom_y)
        pix = page.get_pixmap(matrix=mat)
        img_data = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_data))
        images.append(img)
    state["ans_sheet"] = images
    return state


################################################################################
################################################################################
## LLM Image Analysis
################################################################################
################################################################################

def llm_images(images):
    image_obj = []
    for img in images:
        buff = io.BytesIO()
        img.save(buff, format="PNG")
        img_str = base64.b64encode(buff.getvalue()).decode("utf-8")
        image_obj.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{img_str}"}
        })
    return image_obj

################################################################################
################################################################################
## Total Marks Extraction
################################################################################
################################################################################

def extract_total_marks(text: str) -> int:
    """Robust total marks extractor that works across multiple formats."""
    patterns = [
        r'Total Marks Obtained[:\s]+(\d+)',
        r'Marks Obtained[:\s]+(\d+)',
        r'Overall Total[:\s]+(\d+)',
        r'Grand Total[:\s]+(\d+)',
        r'Total[:\s]+(\d+)',
        r'Total\s*=\s*(\d+)',
        r'Score\s*[:=]\s*(\d+)',
        r'(\d+)\s*/\s*\d+'  
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return 0

################################################################################
################################################################################
## AI Answer Evaluation
################################################################################
################################################################################

def llm_evaluator(state: State):
    """Step 1: AI evaluates answers and shows optimized score."""
    subject = state["subject"]
    st_id = state["Studentid"]
    images = state["ans_sheet"]
    images_for_llm = llm_images(images)
    retry_count = state.get("retry_count", 0)
    teacher_feedback = state.get("teacher_feedback", "")

    print(f"Evaluating Attempt #{retry_count + 1} ({subject})")

    system_content = (
        """You are a senior university examiner with years of experience in checking handwritten answer sheets. 
Your job is to evaluate the student’s answers in a strict, fair, and highly structured manner.

-----------------------------------------
📘 PART 1 — GENERAL EVALUATION PRINCIPLES
-----------------------------------------
1. Evaluate strictly based on:
   - Understanding of the concept
   - Relevance to the question
   - Depth of explanation
   - Correctness of information
   - Structure and organization of the answer
   - Clarity of reasoning
   - Use of examples, diagrams, formulas (if applicable)
   - Completeness (covering all key points)

2. If the answer is partially correct → award partial marks.
3. If the answer is mostly irrelevant → award very low marks.
4. If an answer is empty, crossed out, or missing → give 0 marks.
5. Handwriting quality should NOT affect marks.
6. If the image text is unclear, state: 
   "Part of the answer is unreadable, marks awarded based only on visible content."

-----------------------------------------
📘 PART 2 — HOW TO GIVE MARKS PER QUESTION
-----------------------------------------
For each question, use the following marking guide:

🟩 Full Marks (Excellent Answer)
- All key concepts covered
- Explanation is clear, logical, and accurate
- Examples or diagrams support the answer
- Structured well (Introduction → Body → Conclusion)
- Shows conceptual understanding
→ Award full marks (ex: 7/7)

🟨 4–6 Marks (Good Answer)
- Mostly correct but missing small details
- Explanation is acceptable but not very deep
- Examples may be missing
→ Award 4–6 marks depending on completeness

🟧 1–3 Marks (Weak Answer)
- Very short answer
- Only 1–2 keywords mentioned
- Lacks explanation / reasoning
→ Award 1–3 marks

🟥 0 Marks (Incorrect Answer)
- Completely wrong / irrelevant
- No attempt
- Cannot interpret handwriting
→ Award 0

-----------------------------------------
📘 PART 3 — STRUCTURE & PRESENTATION RULES
-----------------------------------------
Give higher marks for answers that:
- Start with a small definition or intro
- Provide clear bullet points or steps
- Use relevant examples or formulas
- Present diagrams (even roughly)
- Summarize in 1–2 lines

Deduct marks if:
- Answer is unstructured and confusing
- The student lists points with no explanation
- The student repeats the question without adding value
- Important concepts are missing

-----------------------------------------
📘 PART 4 — HOW TO CHECK ANSWER QUALITY
-----------------------------------------
For each answer:
1. Determine if the student understood the concept.
2. Identify the important keywords that should appear.
3. See if the explanation is correct and complete.
4. Evaluate if the answer follows a structured flow.
5. Award marks according to the marking guide.

-----------------------------------------
📘 PART 5 — SCORING RULES
-----------------------------------------
- Each question carries exactly 7 marks.
- Compute Total Marks as:
  
      Total = Sum of all question scores

- Do NOT scale or adjust marks unless instructed.
- Do NOT assume a fixed total. 
  Only calculate total based on number of answers.

-----------------------------------------
📘 PART 6 — OUTPUT FORMAT (VERY IMPORTANT)
-----------------------------------------
Always produce output in this structure:

### Question-wise Evaluation
Q1:
- Expected answer summary: <2–3 lines>
- Student answer analysis: <your analysis>
- Marks Awarded: X/7

Q2:
- Expected answer summary:
- Student answer analysis:
- Marks Awarded: X/7

...(Continue for all questions)

### Final Summary
- Total Questions Evaluated: N
- Total Marks Obtained: X
- Maximum Possible Marks: N × 7
- Overall Feedback: <Short 3–5 lines on strengths, weaknesses, improvements>

-----------------------------------------
📘 PART 7 — GENERAL RULES
-----------------------------------------
- Be strict but fair.
- Justify every mark you award.
- Never hallucinate content that is not visible.
- If an answer is unclear, state so explicitly.
- Only evaluate what is visually present.

-----------------------------------------
BEGIN EVALUATION NOW.
-----------------------------------------
"""
    )

    if retry_count > 0 and teacher_feedback:
        system_content += f"\nTeacher Feedback:\n{teacher_feedback}\n"

    messages = [
        SystemMessage(content=system_content),
        HumanMessage(content=[{"type": "text", "text": f"Evaluate the paper for {subject}, ID: {st_id}"}] + images_for_llm),
    ]

    response = llm.invoke(messages)
    resp_text = response.content if hasattr(response, 'content') else str(response)
    total_marks = extract_total_marks(resp_text)

    adjusted_marks = evaluation_optimizer(total_marks, mode="inference")
    
    ################################################################################
    ################################################################################
    # Show THe relevant information
    ################################################################################
    ################################################################################

    print("\n" + "=" * 70)
    print("LLM Evaluation (Answers & Scores)")
    print("=" * 70)
    print(resp_text)
    print("\n" + "=" * 70)
    print(f"AI Optimized Total Score: {adjusted_marks} ")
    print("=" * 70)

    return {
        "messages": messages + [response],
        "resp": resp_text,
        "total_marks": total_marks,
        "modified_marks": adjusted_marks,
        "retry_count": retry_count + 1
    }


################################################################################
################################################################################
## Human In The Loop Stage
################################################################################
################################################################################

def human_review(state: State):
    """Step 2: Teacher review."""
    print("\n" + "=" * 70)
    print(f"Teacher Review — Attempt #{state.get('retry_count', 1)}")
    print("=" * 70)
    print(f"AI Suggested (Optimized): {state['modified_marks']} \n")
    print("=" * 70)

    while True:
        choice = input("\nDecision (approve / reject / modify:XXX / cancel): ").strip().lower()
        if choice == 'approve':
            return {"teacher_approved": True, "teacher_feedback": "Approved", "needs_reevaluation": False}
        elif choice == 'reject':
            feedback = input("Enter feedback for re-evaluation: ").strip()
            return {"teacher_approved": False, "teacher_feedback": feedback, "needs_reevaluation": True}
        elif choice.startswith('modify:'):
            try:
                new_marks = int(choice.split(':')[1])
                return {"teacher_approved": True, "modified_marks": new_marks, "teacher_feedback": f"Modified to {new_marks}", "needs_reevaluation": False}
            except:
                print("Invalid format. Use modify:XXX")
        elif choice == 'cancel':
            return {"teacher_approved": False, "teacher_feedback": "Cancelled", "needs_reevaluation": False}
        else:
            print("Invalid input.")


################################################################################
################################################################################
## Review Routing Logic
################################################################################
################################################################################

def route_after_review(state: State):
    if state.get("needs_reevaluation", False) and state.get("retry_count", 0) < 3:
        return "llm_answer_checker"
    else:
        return "save_to_excel"

################################################################################
################################################################################
## Save Results to Excel
################################################################################
################################################################################

def save_to_excel(state: State):
    """Step 3: Save results + update optimizer."""
    if not state.get("teacher_approved", False):
        print("\nEvaluation not approved. Skipping save.")
        return {"resp": state["resp"]}

    ai_score = state["total_marks"]
    teacher_score = state.get("modified_marks", ai_score)


    evaluation_optimizer(ai_score, teacher_score, mode="update")

    result = update_marks.invoke({
        "file_path": "student_marks.xlsx",
        "student_id": state["Studentid"],
        "subject": state["subject"],
        "marks": teacher_score
    })

    print(f"Saved final marks: {teacher_score}")
    print(f"Current Optimizer Memory: {optimizer}")
    return {"resp": state["resp"] + f"\nFinal Marks: {teacher_score}\n{result}"}

################################################################################
################################################################################
## Email Result Sender
################################################################################
################################################################################

def email_sender(state: State):

    ################################################################################
    ################################################################################
    ## Email Sending Step
    ################################################################################
    ################################################################################
 
    print("\nDo you want to send this result via email to the student?")
    choice = input("Type 'yes' or 'no': ").strip().lower()

    if choice == "yes":
        to_email = input("Enter student's email address: ").strip()


        improvement_text = ""
        try:
            print("Generating personalized improvement feedback for the student...")
            improvement_prompt = [
                SystemMessage(content="You are a helpful teacher. Based on the evaluation text below, summarize in 2–3 sentences what the student can improve in future answers."),
                HumanMessage(content=state.get("resp", ""))
            ]
            llm_response = llm.invoke(improvement_prompt)
            improvement_text = getattr(llm_response, "content", str(llm_response)).strip()
        except Exception as e:
            print(f"Could not generate improvement feedback: {e}")
            improvement_text = "Focus on understanding key concepts and improving weak areas."


        if not improvement_text:
            improvement_text = "Focus on understanding key concepts and improving weak areas."
         
        

        def send_email(to_email, student_name, subject, marks, teacher_feedback,resp_text, improvement_text):
            sender = "saraarekar@gmail.com"  
            password =os.getenv("GMAIL_APP_PASSWORD") 

            email_subject = f"Your {subject} Evaluation Result"
            body = f"""
Hello {student_name},

Your paper for the subject "{subject}" 
has been evaluated.
Marks obtained: {marks}
Feedback: {teacher_feedback}
        
----------------------
What You Can Improve
----------------------
{improvement_text}

Regards,
Evaluator
"""
            body = textwrap.dedent(body).strip()

            file_name = f"{student_name.replace(' ', '_')}_feedback.txt"
            with open(file_name, "w", encoding="utf-8") as f:
               f.write(f"AI Detailed Evaluation for {student_name} - {subject}\n")
               f.write("=" * 50 + "\n\n")
               f.write(resp_text.strip())


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

    
            print("\nPreparing to send email...")
            print(f"From: {sender}")
            print(f"To:   {to_email}")
            print(f"Subject: {email_subject}")

            try:
            
                print("Connecting to Gmail server...")
                with smtplib.SMTP("smtp.gmail.com", 587) as server:
                    server.starttls()
                    print("Secure connection established.")
                    server.login(sender, password)
                    print("Logged into Gmail successfully.")


                    server.send_message(msg)
                    print(f"Email sent successfully to {to_email}!\n")


                    with open("email_log.txt", "a", encoding="utf-8") as log:
                        log.write(f"[{datetime.datetime.now()}] Sent to {to_email} | Subject: {subject}\n")

            except smtplib.SMTPAuthenticationError:
                print("Authentication failed! Use a Gmail App Password (not your normal password).")
                with open("email_log.txt", "a", encoding="utf-8") as log:
                    log.write(f"[{datetime.datetime.now()}] Auth failed for {sender}\n")

            except Exception as e:
                print(f"Failed to send email: {e}")
                with open("email_log.txt", "a", encoding="utf-8") as log:
                    log.write(f"[{datetime.datetime.now()}] Failed to send to {to_email} | Error: {e}\n")

            finally:

                if os.path.exists(file_name):
                    os.remove(file_name)
        
        resp_text = state.get("resp", "No detailed evaluation available.")

        send_email(
            to_email,
            state.get("Studentid", "Student"),
            state["subject"],
            state.get("modified_marks", state.get("total_marks")),
            state.get("teacher_feedback", "No feedback provided."),
            resp_text,
            improvement_text
        )
        return {"resp": state["resp"]}
    else:
        print("Email skipped by user.")
        return {"resp": state["resp"]}
    
################################################################################
################################################################################
## Auto Approval Excel Saver (Streamlit Mode)
################################################################################
################################################################################

def save_to_excel_auto(state: State):
    """Auto-approve version for Streamlit (no human interaction)."""
    ai_score = state["total_marks"]
    adjusted_score = state.get("modified_marks", ai_score)
    
    
    print(f"Auto-saving marks: {adjusted_score} (Streamlit mode)")
    
    return {
        "teacher_approved": True,  
        "resp": state["resp"]
    }

################################################################################
################################################################################
## Main Evaluation Workflow Graph
################################################################################
################################################################################

graph.add_node("input_node", take_info)
graph.add_node("pdf_processor", pdf_to_image)
graph.add_node("llm_answer_checker", llm_evaluator)
graph.add_node("human_review", human_review)
graph.add_node("save_to_excel", save_to_excel)
graph.add_node("email_sender", email_sender)
graph.add_edge(START, "input_node")
graph.add_edge("input_node", "pdf_processor")
graph.add_edge("pdf_processor", "llm_answer_checker")
graph.add_edge("llm_answer_checker", "human_review")
graph.add_conditional_edges("human_review", route_after_review, {
    "llm_answer_checker": "llm_answer_checker",
    "save_to_excel": "save_to_excel"
})
graph.add_edge("save_to_excel", "email_sender")
graph.add_edge("email_sender", END)

workflow = graph.compile()

################################################################################
################################################################################
## Streamlit Evaluation Workflow (Auto Mode)
################################################################################
################################################################################

streamlit_graph = StateGraph(State)
streamlit_graph.add_node("input_node", take_info)
streamlit_graph.add_node("pdf_processor", pdf_to_image)
streamlit_graph.add_node("llm_answer_checker", llm_evaluator)
streamlit_graph.add_node("save_to_excel_auto", save_to_excel_auto) 

streamlit_graph.add_edge(START, "input_node")
streamlit_graph.add_edge("input_node", "pdf_processor")
streamlit_graph.add_edge("pdf_processor", "llm_answer_checker")
streamlit_graph.add_edge("llm_answer_checker", "save_to_excel_auto")  
streamlit_graph.add_edge("save_to_excel_auto", END)

streamlit_workflow = streamlit_graph.compile()

################################################################################
################################################################################
## Workflow Execution Entry Point
################################################################################
################################################################################
if __name__ == "__main__":
    print("Starting Evaluation Workflow...")
    out = workflow.invoke({})
    print("\n" + "=" * 70)
    print("FINAL RESULT")
    print("=" * 70)
    print(out.get('resp', 'No response'))
