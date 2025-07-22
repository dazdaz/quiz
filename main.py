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
SERVICE_ACCOUNT_EMAIL = "quiz-reader@project.iam.gserviceaccount.com"
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

# --- MODIFIED FUNCTION ---
def parse_questions(text):
    """
    Parses raw text into a list of questions using a robust method
    that is not dependent on strict line ordering.
    """
    if not text or '---START' not in text:
        return []

    content_block = text.split('---START', 1)[1]
    questions = []

    # Split the text into blocks, where each block starts with a number like "1:", "2.", etc.
    question_blocks = re.split(r'\n(?=\s*\d+[:.\s])', content_block)

    for block in question_blocks:
        lines = [line.strip() for line in block.strip().split('\n') if line.strip()]
        if not lines:
            continue

        q_text = ''
        options = []
        correct_answer = ''
        description = ''

        # The first line is assumed to be the question text
        q_text_line = lines.pop(0)
        q_text = re.split(r'^\s*\d+[:.\s]+', q_text_line, 1)[-1].strip()

        # Iterate through the rest of the lines to find components by their prefix
        for line in lines:
            line_lower = line.lower()
            if line_lower.startswith('answer:'):
                correct_answer = line.split(':', 1)[1].strip().upper()
            elif line_lower.startswith('description:'):
                description = line.split(':', 1)[1].strip()
            # Assume any line starting with a letter and a parenthesis is an option
            elif re.match(r'^[a-zA-Z]\)', line):
                options.append(re.sub(r'^[a-zA-Z]\)\s*', '', line).strip())

        # Validate that we found all necessary components before adding the question
        if q_text and len(options) == 4 and correct_answer and description:
            questions.append({
                'question': q_text,
                'options': options,
                'correct': correct_answer,
                'description': description
            })
        else:
            # This will print to your server log if a question is formatted incorrectly
            print(f"Skipping malformed question block (missing components): {block}")

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
        <p>You will have {int(QUIZ_DURATION_SECONDS / 60)} minutes to complete the quiz.</p>
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
        return render_template_string("<h1>Error</h1><p>No questions could be loaded from the source. Please check the document format and server logs.</p>")

    question_indices = list(range(len(ALL_QUESTIONS_CACHE)))
    random.shuffle(question_indices)

    session['quiz_indices'] = question_indices[:NUM_QUESTIONS]
    session['answers'] = {}
    session['current'] = 0
    session['start_time'] = time.time()

    return redirect(url_for('question'))

@app.route('/question', methods=['GET', 'POST'])
def question():
    """Displays the current question and handles answer submission."""
    if 'quiz_indices' not in session or 'start_time' not in session:
        return redirect(url_for('home'))

    current_step = session.get('current', 0)
    quiz_indices = session['quiz_indices']

    if current_step >= len(quiz_indices):
        return redirect(url_for('summary'))

    if request.method == 'POST':
        session['answers'][str(current_step)] = request.form.get('answer')

        if request.form.get('action') == 'end':
            session.modified = True
            return redirect(url_for('summary'))

        session['current'] = current_step + 1
        session.modified = True

        if session['current'] >= len(quiz_indices):
            return redirect(url_for('summary'))
        return redirect(url_for('question'))

    question_index = quiz_indices[current_step]
    q = ALL_QUESTIONS_CACHE[question_index]

    html = f"""
        <!DOCTYPE html><html><head><title>Quiz Question</title><style>
        body {{ font-family: sans-serif; margin: 20px; }}
        #timer {{ position: fixed; top: 10px; right: 20px; font-size: 1.5em; font-weight: bold; color: #d9534f; }}
        form label {{ display: block; margin: 10px; font-size: 1.1em; }}
        .button-container {{ margin-top: 25px; }}
        .button {{ padding: 10px 20px; font-size: 1em; cursor: pointer; border-radius: 5px; border: 1px solid #ccc; }}
        .button-next {{ background-color: #f0f0f0; }}
        .button-end {{ margin-left: 15px; color: white; background-color: #d9534f; border-color: #d43f3a;}}
        </style></head><body><div id="timer"></div>
        <h1>Question {current_step + 1} of {len(quiz_indices)}</h1><h2>{q['question']}</h2>
        <form method="post" id="quiz-form">
    """
    for i, opt in enumerate(q['options']):
        label = chr(65 + i)
        html += f'<label><input type="radio" name="answer" value="{label}" required> {label}) {opt}</label>'

    html += f"""
            <div class="button-container">
                <button type="submit" name="action" value="next" class="button button-next">Next Question</button>
                <button type="submit" name="action" value="end" class="button button-end"
                        onclick="return confirm('Are you sure you want to end the exam?');">End Exam</button>
            </div>
            </form>
            <script>
                const endTime = {session['start_time']} + {QUIZ_DURATION_SECONDS};
                const timerElement = document.getElementById('timer');
                const form = document.getElementById('quiz-form');

                const timerInterval = setInterval(() => {{
                    const remaining = Math.round(endTime - (Date.now() / 1000));
                    if (remaining <= 0) {{
                        timerElement.textContent = "Time's up!";
                        clearInterval(timerInterval);
                        let input = document.createElement('input');
                        input.type = 'hidden';
                        input.name = 'action';
                        input.value = 'end';
                        form.appendChild(input);
                        form.submit();
                    }} else {{
                        const minutes = Math.floor(remaining / 60);
                        const seconds = remaining % 60;
                        timerElement.textContent = `${{minutes.toString().padStart(2, '0')}}:${{seconds.toString().padStart(2, '0')}}`;
                    }}
                }}, 1000);
            </script></body></html>
    """
    return render_template_string(html)

@app.route('/summary')
def summary():
    """Calculates and displays the quiz summary."""
    if 'quiz_indices' not in session:
        return redirect(url_for('home'))

    session.pop('start_time', None)
    quiz_indices = session.get('quiz_indices', [])
    answers = session.get('answers', {})
    correct_count = 0
    incorrect = []

    for i, question_index in enumerate(quiz_indices):
        q = ALL_QUESTIONS_CACHE[question_index]
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
import os
import re
import random
import time

app = Flask(__name__)
NUM_QUESTIONS = 15
QUIZ_DURATION_SECONDS = 3600 # 60 minutes
ALL_QUESTIONS_CACHE = None

    DOC_ID = extract_doc_id(DOC_URL)
    if not content:
        return text
                if 'textRun' in elem:
                    text += elem.get('textRun').get('content', '')
    return text

def parse_questions(text):
    """
    Parses raw text into a list of questions using a robust method
    if not text or '---START' not in text:
    content_block = text.split('---START', 1)[1]
    questions = []
    for block in question_blocks:
        q_text = re.split(r'^\s*\d+[:.\s]+', q_text_line, 1)[-1].strip()

        # Iterate through the rest of the lines to find components by their prefix
        for line in lines:
            line_lower = line.lower()
            if line_lower.startswith('answer:'):

                'options': options[:4], # Ensure only 4 options are used
            })
            print(f"Skipping malformed question block (missing components): {block}")


        <!DOCTYPE html>
    """
def start_quiz():
            if not service or not DOC_ID: raise RuntimeError("Google API service not initialized.")

    session['start_time'] = time.time()

    return redirect(url_for('question'))
def question():
    """Displays the current question and handles answer submission."""
    if 'quiz_indices' not in session or 'start_time' not in session:
        return redirect(url_for('home'))

        session['answers'][str(current_step)] = request.form.get('answer')

        if request.form.get('action') == 'end':
        session['current'] = current_step + 1
        session.modified = True

        return redirect(url_for('question'))

    question_index = quiz_indices[current_step]
    q = ALL_QUESTIONS_CACHE[question_index]

        body {{ font-family: sans-serif; margin: 20px; }}
        #timer {{ position: fixed; top: 10px; right: 20px; font-size: 1.5em; font-weight: bold; color: #d9534f; }}
        .button {{ padding: 10px 20px; font-size: 1em; cursor: pointer; border-radius: 5px; border: 1px solid #ccc; }}
        .button-next {{ background-color: #f0f0f0; }}
        .button-end {{ margin-left: 15px; color: white; background-color: #d9534f; border-color: #d43f3a;}}
                <button type="submit" name="action" value="next" class="button button-next">Next Question</button>
            </form>

                const timerInterval = setInterval(() => {{
                        input.type = 'hidden';
                    }} else {{
                    }}
                }}, 1000);
            </script></body></html>
    """
    return render_template_string(html)
    session.pop('start_time', None)
    correct_count = 0
    for i, question_index in enumerate(quiz_indices):
        q = ALL_QUESTIONS_CACHE[question_index]
        user_ans = answers.get(str(i))
        if user_ans == q['correct']:
            correct_count += 1
        else:
            incorrect.append({
                'index': i + 1, 'question': q['question'], 'options': q['options'],
                'correct': q['correct'], 'user': user_ans or 'Not Answered', 'description': q['description']
import os
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
    if not incorrect:
        html += '<p>Congratulations, you had no incorrect answers!</p>'
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
daev-macbookpro2:quiz daev$
daev-macbookpro2:quiz daev$ vim main.py
daev-macbookpro2:quiz daev$
daev-macbookpro2:quiz daev$
daev-macbookpro2:quiz daev$ cat main.py
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
DOC_URL = "https://docs.google.com/document/d/1uXF5Az_GbrzPCnM6_roZGbbEe-kuTyb_V9weWNA1Sls/edit"
SERVICE_ACCOUNT_EMAIL = "quiz-reader@daev-playground.iam.gserviceaccount.com"
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
    """
    Parses raw text into a list of questions using a robust method
    that is not dependent on strict line ordering.
    """
    if not text or '---START' not in text:
        return []

    content_block = text.split('---START', 1)[1]
    questions = []

    # Split the text into blocks, where each block starts with a number like "1:", "2.", etc.
    question_blocks = re.split(r'\n(?=\s*\d+[:.\s])', content_block)

    for block in question_blocks:
        lines = [line.strip() for line in block.strip().split('\n') if line.strip()]
        if not lines:
            continue

        q_text = ''
        options = []
        correct_answer = ''
        description = ''

        # The first line is assumed to be the question text
        q_text_line = lines.pop(0)
        q_text = re.split(r'^\s*\d+[:.\s]+', q_text_line, 1)[-1].strip()

        # Iterate through the rest of the lines to find components by their prefix
        for line in lines:
            line_lower = line.lower()
            if line_lower.startswith('answer:'):
                correct_answer = line.split(':', 1)[1].strip().upper()
            elif line_lower.startswith('description:'):
                description = line.split(':', 1)[1].strip()
            # Assume any line starting with a letter and a parenthesis is an option
            elif re.match(r'^[a-zA-Z]\)', line):
                options.append(re.sub(r'^[a-zA-Z]\)\s*', '', line).strip())

        # Validate that we found all necessary components before adding the question
        if q_text and len(options) >= 4 and correct_answer and description:
            questions.append({
                'question': q_text,
                'options': options[:4], # Ensure only 4 options are used
                'correct': correct_answer,
                'description': description
            })
        else:
            # This will print to your server log if a question is formatted incorrectly
            print(f"Skipping malformed question block (missing components): {block}")

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
        <p>You will have {int(QUIZ_DURATION_SECONDS / 60)} minutes to complete the quiz.</p>
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
        return render_template_string("<h1>Error</h1><p>No questions could be loaded from the source. Please check the document format and server logs.</p>")

    question_indices = list(range(len(ALL_QUESTIONS_CACHE)))
    random.shuffle(question_indices)

    session['quiz_indices'] = question_indices[:NUM_QUESTIONS]
    session['answers'] = {}
    session['current'] = 0
    session['start_time'] = time.time()

    return redirect(url_for('question'))

@app.route('/question', methods=['GET', 'POST'])
def question():
    """Displays the current question and handles answer submission."""
    if 'quiz_indices' not in session or 'start_time' not in session:
        return redirect(url_for('home'))

    current_step = session.get('current', 0)
    quiz_indices = session['quiz_indices']

    if current_step >= len(quiz_indices):
        return redirect(url_for('summary'))

    if request.method == 'POST':
        session['answers'][str(current_step)] = request.form.get('answer')

        if request.form.get('action') == 'end':
            session.modified = True
            return redirect(url_for('summary'))

        session['current'] = current_step + 1
        session.modified = True

        if session['current'] >= len(quiz_indices):
            return redirect(url_for('summary'))
        return redirect(url_for('question'))

    question_index = quiz_indices[current_step]
    q = ALL_QUESTIONS_CACHE[question_index]

    html = f"""
        <!DOCTYPE html><html><head><title>Quiz Question</title><style>
        body {{ font-family: sans-serif; margin: 20px; }}
        #timer {{ position: fixed; top: 10px; right: 20px; font-size: 1.5em; font-weight: bold; color: #d9534f; }}
        form label {{ display: block; margin: 10px; font-size: 1.1em; }}
        .button-container {{ margin-top: 25px; }}
        .button {{ padding: 10px 20px; font-size: 1em; cursor: pointer; border-radius: 5px; border: 1px solid #ccc; }}
        .button-next {{ background-color: #f0f0f0; }}
        .button-end {{ margin-left: 15px; color: white; background-color: #d9534f; border-color: #d43f3a;}}
        </style></head><body><div id="timer"></div>
        <h1>Question {current_step + 1} of {len(quiz_indices)}</h1><h2>{q['question']}</h2>
        <form method="post" id="quiz-form">
    """
    for i, opt in enumerate(q['options']):
        label = chr(65 + i)
        html += f'<label><input type="radio" name="answer" value="{label}" required> {label}) {opt}</label>'

    html += f"""
            <div class="button-container">
                <button type="submit" name="action" value="next" class="button button-next">Next Question</button>
                <button type="submit" name="action" value="end" class="button button-end"
                        onclick="return confirm('Are you sure you want to end the exam?');">End Exam</button>
            </div>
            </form>
            <script>
                const endTime = {session.get('start_time', time.time())} + {QUIZ_DURATION_SECONDS};
                const timerElement = document.getElementById('timer');
                const form = document.getElementById('quiz-form');

                const timerInterval = setInterval(() => {{
                    const remaining = Math.round(endTime - (Date.now() / 1000));
                    if (remaining <= 0) {{
                        timerElement.textContent = "Time's up!";
                        clearInterval(timerInterval);
                        let input = document.createElement('input');
                        input.type = 'hidden';
                        input.name = 'action';
                        input.value = 'end';
                        form.appendChild(input);
                        form.submit();
                    }} else {{
                        const minutes = Math.floor(remaining / 60);
                        const seconds = remaining % 60;
                        timerElement.textContent = `${{minutes.toString().padStart(2, '0')}}:${{seconds.toString().padStart(2, '0')}}`;
                    }}
                }}, 1000);
            </script></body></html>
    """
    return render_template_string(html)

@app.route('/summary')
def summary():
    """Calculates and displays the quiz summary."""
    if 'quiz_indices' not in session:
        return redirect(url_for('home'))

    session.pop('start_time', None)
    quiz_indices = session.get('quiz_indices', [])
    answers = session.get('answers', {})
    correct_count = 0
    incorrect = []

    for i, question_index in enumerate(quiz_indices):
        q = ALL_QUESTIONS_CACHE[question_index]
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
    if not incorrect:
        html += '<p>Congratulations, you had no incorrect answers!</p>'
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
