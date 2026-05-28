# # streamlit_app.py
# import streamlit as st
# import tempfile
# import os
# import re
# import pandas as pd
# from pathlib import Path

# # Import your existing agent (no changes to agent.py required).
# # Make sure agent.py is in the same folder as this streamlit app.
# import agent as agent_module  # :contentReference[oaicite:1]{index=1}

# st.set_page_config(page_title="Answer Sheet Evaluator", layout="centered")

# st.title("📘 Answer Sheet Evaluator (Streamlit frontend)")
# st.markdown(
# "---------"
# )
# # --- Inputs ---
# with st.form(key="eval_form"):
#     student_id = st.text_input("Student ID", value="", placeholder="e.g. 2025CS101")
#     subject = st.text_input("Subject", value="Data Analytics")
#     uploaded_file = st.file_uploader("Browse PDF answer-sheet", type=["pdf"])
#     start_eval = st.form_submit_button("Start evaluation")

# # helper: parse maximum marks from LLM text
# def parse_max_marks(resp_text: str, total_marks: int) -> int:
#     # Try explicit patterns first
#     patterns = [
#         r"Maximum Possible Marks[:\s]+(\d+)",
#         r"Maximum Possible Marks[:\s]+(\d+)\s*[x×]\s*(\d+)",
#         r"Maximum\s*Marks[:\s]+(\d+)",
#         r"Maximum\s*Possible[:\s]+(\d+)",
#         r"Maximum\s*Possible\s*Marks\s*[:=]\s*(\d+)"
#     ]
#     for pat in patterns:
#         m = re.search(pat, resp_text, re.IGNORECASE)
#         if m:
#             # If pattern captures two groups (N x 7) try to compute product
#             if len(m.groups()) >= 2 and m.group(2):
#                 try:
#                     return int(m.group(1)) * int(m.group(2))
#                 except:
#                     pass
#             try:
#                 return int(m.group(1))
#             except:
#                 pass

#     # Fallback: count questions like "Q1:" and multiply by 7 (matches agent's rubric).
#     q_count = len(re.findall(r'\bQ\d+\b\s*:', resp_text))
#     if q_count > 0:
#         return q_count * 7

#     # If none found, fallback to total_marks (so percentage = 100%)
#     return total_marks if total_marks and total_marks > 0 else 0


# # session state to store last result
# if "last_state" not in st.session_state:
#     st.session_state["last_state"] = None

# if start_eval:
#     if not student_id:
#         st.error("Please enter Student ID.")
#     elif uploaded_file is None:
#         st.error("Please upload a PDF answer sheet.")
#     else:
#         # Save uploaded file to a temp path (agent.pdf_to_image expects a filesystem path)
#         tmp_dir = tempfile.mkdtemp(prefix="eval_")
#         tmp_path = os.path.join(tmp_dir, uploaded_file.name)
#         with open(tmp_path, "wb") as f:
#             f.write(uploaded_file.read())

#         st.info("Saved uploaded file. Starting AI evaluation (this may take some time)...")

#         # Build state dict expected by agent.py functions
#         state = {
#             "Studentid": student_id,
#             "subject": subject,
#             "ans_sheet_path": tmp_path,
#             "teacher_approved": False,
#             "retry_count": 0,
#             "needs_reevaluation": False
#         }

#         # Step A: convert PDF -> images using the agent's function
#         try:
#             # pdf_to_image returns state with "ans_sheet"
#             state = agent_module.pdf_to_image(state)
#         except Exception as e:
#             st.exception(f"pdf_to_image failed: {e}")
#             raise

#         # Step B: call llm evaluator (AI evaluation). It returns dict with resp, total_marks, modified_marks
#         try:
#             eval_result = agent_module.llm_evaluator(state)
#         except Exception as e:
#             st.exception(f"AI evaluation failed (llm.invoke may need API keys): {e}")
#             raise

#         # store results in session
#         st.session_state["last_state"] = {
#             "state": state,
#             "eval_result": eval_result,
#             "pdf_path": tmp_path
#         }

# import re


# def infer_question_count(text: str) -> int:
#     # Count Q1, Q2, Question 1, Part (a) style hints
#     q1 = len(re.findall(r'\bQ\d+\b', text, re.IGNORECASE))
#     q2 = len(re.findall(r'\bQuestion\s+\d+\b', text, re.IGNORECASE))
#     return max(q1, q2, 0)

# def parse_max_marks(resp_text: str, ai_total: float) -> int:
#     """
#     Try several patterns to find 'max marks' in LLM text. If none found,
#     fallback to question-count * 7 (if questions detected). Otherwise 0.
#     """
#     if not resp_text:
#         return 0
#     patterns = [
#         r"Out of[:\s]*([0-9]+(?:\.[0-9]+)?)",
#         r"Maximum\s*Marks[:\s]*([0-9]+(?:\.[0-9]+)?)",
#         r"Total\s*marks\s*(possible|out of)[:\s]*([0-9]+(?:\.[0-9]+)?)",
#         r"Total\s*possible[:\s]*([0-9]+(?:\.[0-9]+)?)",
#         r"Max[:\s]*([0-9]+(?:\.[0-9]+)?)",
#     ]
#     for pat in patterns:
#         m = re.search(pat, resp_text, re.IGNORECASE)
#         if m:
#             # some patterns have group(1) meaningful, others group(2)
#             for g in m.groups():
#                 if g:
#                     try:
#                         return int(float(g))
#                     except:
#                         continue

#     # fallback: infer by question count * 7 (7 is used in your original app heuristics)
#     q_count = infer_question_count(resp_text)
#     if q_count > 0:
#         return q_count * 7

#     # if nothing found, fall back to ai_total if ai_total looks like a plausible max
#     try:
#         at = float(ai_total)
#         # if ai_total is an integer and small-ish, assume it's max
#         if at.is_integer() and at > 0 and at < 200:  
#             return int(at)
#     except:
#         pass

#     return 0

# def compute_marks_and_pct(ai_total_raw, resp_text):
#     """
#     Returns dict: {
#       ai_raw, inferred_max, shown_marks, pct, method, q_count, inferred_full
#     }
#     method: 'no_change' / 'clamped_to_max' / 'normalized_from_raw' / 'clamped_fallback'
#     """
#     # coerce ai_total_raw to float
#     try:
#         ai_raw = float(ai_total_raw)
#     except:
#         # if not parseable use 0
#         ai_raw = 0.0

#     max_marks = parse_max_marks(resp_text, ai_raw)
#     q_count = infer_question_count(resp_text)
#     inferred_full = q_count * 7 if q_count > 0 else None

#     shown = ai_raw
#     method = "no_change"

#     # If max known and ai_raw is > max by a suspicious factor -> normalize or clamp
#     if max_marks > 0:
#         if ai_raw <= max_marks * 1.05:
#             # close enough, keep as-is (allow small rounding differences)
#             shown = round(ai_raw, 2)
#             method = "no_change"
#         else:
#             # ai_raw is much larger than max -> try normalization if we can estimate full marks
#             if inferred_full and inferred_full > 0:
#                 # normalize raw -> scale [0..inferred_full] to [0..max_marks]
#                 shown = round((ai_raw / inferred_full) * max_marks, 2)
#                 method = f"normalized_from_raw (assumed_full={inferred_full})"
#             else:
#                 # no good inference, clamp to max_marks
#                 shown = round(min(ai_raw, max_marks), 2)
#                 method = "clamped_to_max"
#     else:
#         # max not known: if ai_raw seems absurdly large (>1000) clamp to reasonable bound
#         if ai_raw > 1000 and inferred_full:
#             shown = round((ai_raw / inferred_full) * (inferred_full), 2)
#             method = "clamped_fallback"
#         else:
#             shown = round(ai_raw, 2)
#             method = "no_max_info"

#     pct = None
#     if max_marks and max_marks > 0:
#         pct = round((shown / max_marks) * 100, 2)

#     return {
#         "ai_raw": round(ai_raw, 2),
#         "inferred_max": max_marks,
#         "shown_marks": shown,
#         "pct": pct,
#         "method": method,
#         "q_count": q_count,
#         "inferred_full": inferred_full
#     }


# # If there is a previous result show it and teacher controls
# if st.session_state.get("last_state"):
#     data = st.session_state["last_state"]
#     state = data["state"]
#     eval_result = data["eval_result"]
#     resp_text = eval_result.get("resp", "")
#     computed = compute_marks_and_pct(eval_result.get("total_marks", 0), resp_text)

#     ai_total = computed["ai_raw"]
#     ai_adjusted = computed["shown_marks"]
#     max_marks = computed["inferred_max"]
#     pct = computed["pct"]
#     method = computed["method"]
#     q_count = computed["q_count"]
#     inferred_full = computed["inferred_full"]
#     max_marks = parse_max_marks(resp_text, ai_total)
#     pct = None
#     if max_marks > 0:
#         pct = round((ai_adjusted / max_marks) * 100, 2)
#     else:
#         pct = None

#     st.header("Evaluation Result")
#     col1, col2, col3, col4 = st.columns(4)
#     col1.metric("Marks (AI raw)", ai_total)
#     col2.metric("out of", max_marks)
#     col3.metric("Marks (AI adjusted)", ai_adjusted)
#     if max_marks > 0:
#         col4.metric("Percentage", f"{pct}%")
#     else:
#         col4.metric("Percentage", "N/A (max marks unknown)")
#     if method != "no_change":
#         st.warning(f"Note: frontend adjusted AI score using method: {method}. (Questions detected: {q_count}, assumed per-q full marks: 7 -> inferred_full={inferred_full})")
#     with st.expander("Show full LLM evaluation (detailed)"):
#         st.code(resp_text[:100000], language="")

#     st.markdown("### ✍️ Teacher feedback / actions")
#     with st.form("teacher_form"):
#         teacher_feedback = st.text_area("Teacher feedback (if rejecting / or general comments)", value="")
#         modify_marks_str = st.text_input("If you want to modify marks, enter new marks (integer) or leave blank", value="")
#         approve_btn = st.form_submit_button("Approve (accept current adjusted marks)")
#         modify_btn = st.form_submit_button("Modify (use entered marks)")
#         reject_btn = st.form_submit_button("Reject & Re-evaluate (use feedback and ask AI to re-evaluate)")

#     # Helper: attempt to call update_marks (handles both decorated tool having .invoke or plain function)
#     def persist_marks(file_path, student_id, subject, marks):
#         # Try update_marks.invoke(...)
#         try:
#             if hasattr(agent_module.update_marks, "invoke"):
#                 res = agent_module.update_marks.invoke({
#                     "file_path": file_path,
#                     "student_id": student_id,
#                     "subject": subject,
#                     "marks": int(marks)
#                 })
#                 return True, str(res)
#         except Exception as e:
#             # continue to next attempt
#             pass

#         # Try calling as a normal function
#         try:
#             res = agent_module.update_marks(file_path, student_id, subject, int(marks))
#             return True, str(res)
#         except Exception as e:
#             return False, str(e)

#     # Button handlers
#     if approve_btn:
#         teacher_marks = ai_adjusted
#         # Update optimizer using agent function
#         try:
#             agent_module.evaluation_optimizer(ai_total, teacher_marks, mode="update")
#         except Exception as e:
#             st.warning(f"Could not update optimizer: {e}")

#         ok, msg = persist_marks("student_marks.xlsx", state["Studentid"], state["subject"], teacher_marks)
#         if ok:
#             st.success(f"Approved and saved marks: {teacher_marks}. ({msg})")
#         else:
#             st.error(f"Failed to persist marks: {msg}")
#         # show teacher feedback summary
#         st.info(f"Teacher feedback: {teacher_feedback or 'Approved'}")

#     if modify_btn:
#         try:
#             new_marks = int(modify_marks_str)
#         except:
#             st.error("Please enter a valid integer in 'modify marks' field.")
#             new_marks = None

#         if new_marks is not None:
#             # update optimizer & save
#             try:
#                 agent_module.evaluation_optimizer(ai_total, new_marks, mode="update")
#             except Exception as e:
#                 st.warning(f"Could not update optimizer: {e}")

#             ok, msg = persist_marks("student_marks.xlsx", state["Studentid"], state["subject"], new_marks)
#             if ok:
#                 st.success(f"Modified and saved marks: {new_marks}. ({msg})")
#             else:
#                 st.error(f"Failed to persist marks: {msg}")
#             st.info(f"Teacher feedback: {teacher_feedback or 'Modified by teacher.'}")

#     if reject_btn:
#         # Fill teacher_feedback into state and call evaluator again (reevaluation)
#         st.info("Sending teacher feedback to AI and re-evaluating (one attempt)...")
#         state_for_re = dict(state)
#         state_for_re["teacher_feedback"] = teacher_feedback or "Teacher asked for re-evaluation"
#         state_for_re["retry_count"] = state_for_re.get("retry_count", 0) + 1

#         if state_for_re["retry_count"] > 3:
#             st.error("Max re-evaluation attempts reached (3).")
#         else:
#             try:
#                 re_eval = agent_module.llm_evaluator(state_for_re)
#                 # update stored last_state
#                 st.session_state["last_state"]["state"] = state_for_re
#                 st.session_state["last_state"]["eval_result"] = re_eval
#                 st.success("Re-evaluation completed. Refresh the page or re-open the result panel to see updated marks.")
#                 # show some immediate values
#                 st.write("AI re-evaluation marks:", re_eval.get("total_marks"), "Adjusted:", re_eval.get("modified_marks"))
#             except Exception as e:
#                 st.exception(f"Re-evaluation failed: {e}")

#     st.markdown("---")


# frontend_integrated.py