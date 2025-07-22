import os
import re
import random
from flask import Flask, request, session, redirect, url_for, render_template_string
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import google.auth

app = Flask(__name__)
app.secret_key = os.urandom(24)  # Secure random secret key for sessions

# --- Configuration ---
# Your Google Doc URL containing the quiz questions
DOC_URL = "https://docs.google.com/document/d/12234567890/edit"
# The service account email you are using for the Cloud Run service
SERVICE_ACCOUNT_EMAIL = "quiz-reader@my-playground.iam.gserviceaccount.com"


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
    # Use Application Default Credentials, standard for Cloud Run.
    # This automatically finds the service account credentials in the environment.
    SCOPES = ['https://www.googleapis.com/auth/documents.readonly']
    credentials, project = google.auth.default(scopes=SCOPES)
    service = build('docs', 'v1', credentials=credentials)
except (ValueError, google.auth.exceptions.DefaultCredentialsError) as e:
    print(f"Error during initialization: {e}")
    # The service will remain None, which will be handled in the routes.


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

    # Process text after the start marker
    content_block = text.split('---START', 1)[1].strip()
    questions = []

    # A more robust way to split questions using regex.
    # This splits the text at each "digit:", ensuring each question is a separate item.
    # The pattern looks for a newline, followed by one or more digits, and a colon.
    question_blocks = re.split(r'\n(?=\d+:)', content_block)

    for block in question_blocks:
        lines = [line.strip() for line in block.strip().split('\n') if line.strip()]
        if len(lines) < 6:
            continue # Skip malformed blocks

        question_line = lines[0]
        options = lines[1:5]
        correct_line = lines[5]

        if not correct_line.lower().startswith('correct:'):
            continue

        try:
            q_text = question_line.split(':', 1)[1].strip()
            opts = [opt.split(')', 1)[1].strip() for opt in options]
            correct_answer_char = correct_line.split(':', 1)[1].strip().upper()

            questions.append({
                'question': q_text,
                'options': opts,
                'correct': correct_answer_char
            })
        except IndexError:
            # This can happen if a line doesn't have the expected format (e.g., missing a ')' or ':')
            print(f"Skipping malformed question block: {block}")
            continue

    return questions


# --- Flask Routes ---
@app.route('/', methods=['GET'])
def home():
    if not service:
        return render_template_string("<h1>Error</h1><p>Could not initialize Google API service. This might be due to a configuration issue with Application Default Credentials.</p>")
    if not DOC_ID:
        return render_template_string("<h1>Error</h1><p>Invalid Google Doc URL configured in the application.</p>")

    try:
        text = get_document_text(DOC_ID)
        questions = parse_questions(text)

        if not questions:
            error_msg = (
                "<h1>Error</h1>"
                "<p>Failed to load or parse questions from the Google Doc.</p>"
                "<strong>Please check the following:</strong>"
                "<ul>"
                f"<li>Ensure the Google Doc is shared with the service account: <strong>{SERVICE_ACCOUNT_EMAIL}</strong></li>"
                "<li>Ensure the document content starts with <code>---START</code> and follows the correct question format.</li>"
                "</ul>"
            )
            return render_template_string(error_msg)

        # Shuffle the questions randomly 🎲
        random.shuffle(questions)

        session['questions'] = questions
        session['answers'] = {}
        session['current'] = 0
        return redirect(url_for('question'))

    except HttpError as e:
        error_msg = (
            "<h1>Google API Error</h1>"
            f"<p>An error occurred while fetching the document: <strong>{e.reason}</strong> (Code: {e.status_code})</p>"
            "<strong>Please check:</strong>"
            "<ul>"
            f"<li>That the Google Doc at the configured URL exists and is accessible.</li>"
            f"<li>That the document has been shared with <strong>'{SERVICE_ACCOUNT_EMAIL}'</strong> with at least 'Viewer' permissions.</li>"
            "</ul>"
        )
        return render_template_string(error_msg)
    except Exception as e:
        return render_template_string(f"<h1>An Unexpected Error Occurred</h1><p>{e}</p>")


@app.route('/question', methods=['GET', 'POST'])
def question():
    if 'questions' not in session:
        return redirect(url_for('home'))

    current = session.get('current', 0)
    questions = session['questions']

    if current >= len(questions):
        return redirect(url_for('summary'))

    q = questions[current]

    if request.method == 'POST':
        user_answer = request.form.get('answer')
        # Store even if no answer was selected, to show it was skipped
        session['answers'][str(current)] = user_answer
        session['current'] = current + 1
        # Use direct assignment to ensure session is saved
        session.modified = True
        return redirect(url_for('question'))

    # Prepare HTML for the question form
    html = f"""
        <h1>Question {current + 1} of {len(questions)}</h1>
        <h2>{q['question']}</h2>
        <form method="post">
    """
    for i, opt in enumerate(q['options']):
        label = chr(65 + i)  # A, B, C, D
        html += f'<label style="display:block; margin: 10px;"><input type="radio" name="answer" value="{label}" required> {label}) {opt}</label>'
    html += '<br><button type="submit">Next Question</button></form>'
    return render_template_string(html)


@app.route('/summary')
def summary():
    if 'questions' not in session:
        return redirect(url_for('home'))

    questions = session['questions']
    answers = session.get('answers', {})
    correct_count = 0
    incorrect = []

    for i, q in enumerate(questions):
        user_ans = answers.get(str(i))
        if user_ans == q['correct']:
            correct_count += 1
        else:
            incorrect.append({
                'index': i + 1,
                'question': q['question'],
                'options': q['options'],
                'correct': q['correct'],
                'user': user_ans or 'Not Answered'
            })

    total = len(questions)
    wrong_count = total - correct_count
    percent = (correct_count / total * 100) if total > 0 else 0
    session['incorrect'] = incorrect

    html = f"""
        <h1>Quiz Summary</h1>
        <p><strong>Total Questions:</strong> {total}</p>
        <p style="color: green;"><strong>Correct:</strong> {correct_count}</p>
        <p style="color: red;"><strong>Incorrect:</strong> {wrong_count}</p>
        <h2>Score: {percent:.2f}%</h2>
        <br>
        <a href="/review" style="margin-right: 15px;">Review Incorrect Answers</a>
        <a href="/">Restart Quiz</a>
    """
    return render_template_string(html)


@app.route('/review')
def review():
    if 'incorrect' not in session:
        return redirect(url_for('summary'))

    incorrect = session.get('incorrect', [])
    if not incorrect:
        return redirect(url_for('summary'))

    html = '<h1>Incorrect Answers Review</h1>'
    for inc in incorrect:
        html += f'<hr><h2>Question {inc["index"]}: {inc["question"]}</h2>'
        for i, opt in enumerate(inc['options']):
            label = chr(65 + i)
            style = ""
            if label == inc['user']:
                style = 'color: red; font-weight: bold;' # User's wrong answer
            elif label == inc['correct']:
                style = 'color: green; font-weight: bold;' # The correct answer
            html += f'<p style="{style}">{label}) {opt}</p>'

        html += f"<p><strong>Your answer:</strong> {inc['user']}<br><strong>Correct answer:</strong> {inc['correct']}</p>"

    html += '<hr><br><a href="/summary">Back to Summary</a>'
    return render_template_string(html)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    # Set debug=False for production environments like Cloud Run
    app.run(host='0.0.0.0', port=port, debug=False)
