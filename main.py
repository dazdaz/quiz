import os
import re
import random
import time
from flask import Flask, request, session, redirect, url_for, render_template_string
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import google.auth

app = Flask(__name__)
app.secret_key = os.urandom(24) # Secure random secret key for sessions

# --- Configuration ---
DOC_URL = "https://docs.google.com/document/d/1234567890/edit"
SERVICE_ACCOUNT_EMAIL = "quiz-reader@my-project.iam.gserviceaccount.com"
NUM_QUESTIONS = 15
QUIZ_DURATION_SECONDS = 3600 # 60 minutes

# --- Global Cache for Questions ---
ALL_QUESTIONS_CACHE = None

# --- Google API Setup ---
DOC_ID = None
service = None

def extract_doc_id(url):
    """Extracts the Google Doc ID from its URL."""
    match = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
    if match:
        return match.group(1)
    raise ValueError("Invalid Google Doc URL format.")

try:
    DOC_ID = extract_doc_id(DOC_URL)
    SCOPES = ['https://www.googleapis.com/auth/documents.readonly']
    credentials, project = google.auth.default(scopes=SCOPES)
    service = build('docs', 'v1', credentials=credentials)
except (ValueError, google.auth.exceptions.DefaultCredentialsError) as e:
    print(f"Error during initialization: {e}")

# --- Core Functions ---
def get_document_text(doc_id):
    """Fetches text from the Google Doc. Raises HttpError on failure."""
    document = service.documents().get(documentId=doc_id).execute()
    content = document.get('body').get('content')
    text = ''
    if not content:
        return text
    for element in content:
        if 'paragraph' in element:
            elements = element.get('paragraph').get('elements', [])
            for elem in elements:
                if 'textRun' in elem:
                    text += elem.get('textRun').get('content', '')
    return text

def parse_questions(text):
    """Parses the raw text from the document into a list of question dicts."""
    if not text or '---START' not in text:
        return []
    content_block = text.split('---START', 1)[1].strip()
    questions = []
    question_blocks = re.split(r'\n(?=\d+:)', content_block)
    for block in question_blocks:
        lines = [line.strip() for line in block.strip().split('\n') if line.strip()]
        if len(lines) < 7:
            continue
        question_line, options, answer_line, description_line = lines[0], lines[1:5], lines[5], lines[6]
        if not (answer_line.lower().startswith('answer:') and description_line.lower().startswith('description:')):
            continue
        try:
            q_text = question_line.split(':', 1)[1].strip()
            opts = [re.sub(r'^[a-zA-Z]\)\s*', '', opt).strip() for opt in options]
            correct_answer_char = answer_line.split(':', 1)[1].strip().upper()
            description_text = description_line.split(':', 1)[1].strip()
            questions.append({
                'question': q_text, 'options': opts, 'correct': correct_answer_char, 'description': description_text
            })
        except IndexError:
            print(f"Skipping malformed question block: {block}")
            continue
    return questions

# --- Flask Routes ---
@app.route('/')
def home():
    """Displays a simple welcome page."""
    html = f"""
        <!DOCTYPE html>
        <html><head><title>Welcome to the Quiz</title><style>
        body {{ font-family: sans-serif; text-align: center; margin-top: 100px; }}
        .start-button {{ display: inline-block; margin-top: 30px; padding: 15px 30px; font-size: 1.5em; cursor: pointer; text-decoration: none; color: white; background-color: #007BFF; border-radius: 5px; }}
        </style></head><body><h1>Platform Engineering Quiz</h1>
        <p>You will have 60 minutes to complete the quiz.</p>
        <a href="{url_for('start_quiz')}" class="start-button">Start Quiz</a>
        </body></html>
    """
    return render_template_string(html)

@app.route('/start_quiz')
def start_quiz():
    """Initializes quiz, storing only question INDICES in the session."""
    global ALL_QUESTIONS_CACHE
    if ALL_QUESTIONS_CACHE is None:
        try:
            print("INFO: First request, attempting to load questions...")
            if not service or not DOC_ID: raise RuntimeError("Google API service not initialized.")
            text = get_document_text(DOC_ID)
            ALL_QUESTIONS_CACHE = parse_questions(text)
            print(f"SUCCESS: Loaded {len(ALL_QUESTIONS_CACHE)} questions into cache.")
        except Exception as e:
            print(f"CRITICAL: Failed to load questions from Google Doc: {e}")
            ALL_QUESTIONS_CACHE = []
            return render_template_string(f"<h1>Error</h1><p>A critical error occurred: {e}</p>")
    if not ALL_QUESTIONS_CACHE:
        return render_template_string("<h1>Error</h1><p>No questions could be loaded from the source.</p>")

    # Get indices, shuffle them, and store the shuffled list in the session
    question_indices = list(range(len(ALL_QUESTIONS_CACHE)))
    random.shuffle(question_indices)

    session['quiz_indices'] = question_indices[:NUM_QUESTIONS]
    session['answers'] = {}
    session['current'] = 0
    session['start_time'] = time.time()

    return redirect(url_for('question'))

@app.route('/question', methods=['GET', 'POST'])
def question():
    """Displays the current question by looking it up from the server-side cache."""
    if 'quiz_indices' not in session or 'start_time' not in session:
        return redirect(url_for('home'))

    current_step = session.get('current', 0)
    quiz_indices = session['quiz_indices']

    if current_step >= len(quiz_indices):
        return redirect(url_for('summary'))

    if request.method == 'POST':
        session['answers'][str(current_step)] = request.form.get('answer')
        session['current'] = current_step + 1
        session.modified = True
        # Check again if the quiz is over after incrementing
        if session['current'] >= len(quiz_indices):
            return redirect(url_for('summary'))
        return redirect(url_for('question'))

    # Look up the full question details from the server-side cache using the index
    question_index = quiz_indices[current_step]
    q = ALL_QUESTIONS_CACHE[question_index]

    html = f"""
        <!DOCTYPE html><html><head><title>Quiz Question</title><style>
        body {{ font-family: sans-serif; margin: 20px; }}
        #timer {{ position: fixed; top: 10px; right: 20px; font-size: 1.5em; font-weight: bold; color: #d9534f; }}
        form label {{ display: block; margin: 10px; font-size: 1.1em; }}
        </style></head><body><div id="timer"></div>
        <h1>Question {current_step + 1} of {len(quiz_indices)}</h1><h2>{q['question']}</h2>
        <form method="post" id="quiz-form">
    """
    for i, opt in enumerate(q['options']):
        label = chr(65 + i)
        html += f'<label><input type="radio" name="answer" value="{label}" required> {label}) {opt}</label>'
    html += f"""
            <br><button type="submit">Next Question</button></form>
            <script>
                const endTime = {session['start_time']} + {QUIZ_DURATION_SECONDS};
                const timerElement = document.getElementById('timer');
                setInterval(() => {{
                    const remaining = Math.round(endTime - (Date.now() / 1000));
                    if (remaining <= 0) {{
                        timerElement.textContent = "Time's up!";
                        document.getElementById('quiz-form').submit();
                    }} else {{
                        const minutes = Math.floor(remaining / 60);
                        const seconds = remaining % 60;
                        timerElement.textContent = `${{minutes.toString().padStart(2, '0')}}:${{seconds.toString().padStart(2, '0')}}`;
                    }} }}, 1000);
            </script></body></html>
    """
    return render_template_string(html)

@app.route('/summary')
def summary():
    """Calculates and displays the quiz summary."""
    if 'quiz_indices' not in session:
        return redirect(url_for('home'))

    session.pop('start_time', None)
    quiz_indices = session['quiz_indices']
    answers = session.get('answers', {})
    correct_count = 0
    incorrect = []

    for i, question_index in enumerate(quiz_indices):
        q = ALL_QUESTIONS_CACHE[question_index] # Get question from cache
        user_ans = answers.get(str(i))
        if user_ans == q['correct']:
            correct_count += 1
        else:
            incorrect.append({
                'index': i + 1, 'question': q['question'], 'options': q['options'],
                'correct': q['correct'], 'user': user_ans or 'Not Answered', 'description': q['description']
            })

    total = len(quiz_indices)
    percent = (correct_count / total * 100) if total > 0 else 0
    session['incorrect'] = incorrect

    html = f"""
        <h1>Quiz Summary</h1><p><strong>Total Questions:</strong> {total}</p>
        <p style="color: green;"><strong>Correct:</strong> {correct_count}</p>
        <p style="color: red;"><strong>Incorrect:</strong> {total - correct_count}</p>
        <h2>Score: {percent:.2f}%</h2><br>
        <a href="{url_for('review')}">Review Incorrect</a> | <a href="{url_for('start_quiz')}">Restart</a>
    """
    return render_template_string(html)

@app.route('/review')
def review():
    """Displays a review of the incorrect answers."""
    if 'incorrect' not in session:
        return redirect(url_for('summary'))
    incorrect = session.get('incorrect', [])
    html = '<h1>Incorrect Answers Review</h1>'
    for inc in incorrect:
        html += f'<hr><h2>Question {inc["index"]}: {inc["question"]}</h2>'
        for i, opt in enumerate(inc['options']):
            label, style = chr(65 + i), ""
            if label == inc['user']: style = 'color: red; font-weight: bold;'
            elif label == inc['correct']: style = 'color: green; font-weight: bold;'
            html += f'<p style="{style}">{label}) {opt}</p>'
        html += f"""
            <p><strong>Your answer:</strong> {inc['user']}<br><strong>Correct answer:</strong> {inc['correct']}</p>
            <p><strong>Explanation:</strong> {inc['description']}</p>"""
    html += '<hr><br><a href="/summary">Back to Summary</a>'
    return render_template_string(html)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
