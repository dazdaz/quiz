import os
from flask import Flask, request, session, redirect, url_for, render_template_string
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2 import service_account

app = Flask(__name__)
app.secret_key = os.urandom(24)  # Secure random secret key for sessions

# Google Docs API setup
SCOPES = ['https://www.googleapis.com/auth/documents.readonly']
# For Cloud Run, use environment variable or mounted secret for credentials.
# Here, assume 'credentials.json' is in the directory or set GOOGLE_APPLICATION_CREDENTIALS env var.
credentials_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', 'credentials.json')
credentials = service_account.Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
service = build('docs', 'v1', credentials=credentials)

def get_document_text(doc_id):
    try:
        document = service.documents().get(documentId=doc_id).execute()
        content = document.get('body').get('content')
        text = ''
        for element in content:
            if 'paragraph' in element:
                elements = element.get('paragraph').get('elements', [])
                for elem in elements:
                    if 'textRun' in elem:
                        text += elem.get('textRun').get('content', '')
        return text
    except HttpError as e:
        print(f"Error fetching document: {e}")
        return None

def parse_questions(text):
    if not text:
        return []
    start_index = text.find('---START')
    if start_index == -1:
        return []
    text = text[start_index + len('---START'):].strip()
    questions = []
    blocks = text.split('\n\n')
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 6:
            continue
        question = lines[0].strip()
        options = lines[1:5]
        correct_line = lines[5]
        if not correct_line.startswith('Correct: '):
            continue
        correct = correct_line.split('Correct: ')[1].strip()
        q_text = question.split(':', 1)[1].strip() if ':' in question else question
        opts = [opt.split(')', 1)[1].strip() if ')' in opt else opt for opt in options]
        questions.append({
            'question': q_text,
            'options': opts,
            'correct': correct
        })
    return questions

@app.route('/', methods=['GET', 'POST'])
def home():
    if request.method == 'POST':
        doc_id = request.form.get('doc_id')
        text = get_document_text(doc_id)
        questions = parse_questions(text)
        if not questions:
            return render_template_string('<p>Error loading questions. Ensure the document is shared with the service account and follows the format.</p><a href="/">Try again</a>')
        session['questions'] = questions
        session['answers'] = {}
        session['current'] = 0
        return redirect(url_for('question'))
    return render_template_string('''
        <h1>Google Doc Quiz App</h1>
        <form method="post">
            <label>Google Doc ID: <input name="doc_id" required></label><br>
            <button type="submit">Start Quiz</button>
        </form>
        <p>Note: Share the Google Doc with the service account email for access.</p>
    ''')

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
        if user_answer:
            session['answers'][current] = user_answer
            session['current'] = current + 1
        return redirect(url_for('question'))
    html = f'<h2>Question {current+1}: {q["question"]}</h2><form method="post">'
    for i, opt in enumerate(q['options']):
        label = chr(65 + i)  # A, B, C, D
        html += f'<label><input type="radio" name="answer" value="{label}" required> {label}) {opt}</label><br>'
    html += '<button type="submit">Submit</button></form>'
    return render_template_string(html)

@app.route('/summary')
def summary():
    if 'questions' not in session:
        return redirect(url_for('home'))
    questions = session['questions']
    answers = session['answers']
    correct_count = 0
    incorrect = []
    for i, q in enumerate(questions):
        user_ans = answers.get(i)
        if user_ans == q['correct']:
            correct_count += 1
        else:
            incorrect.append({
                'index': i + 1,
                'question': q['question'],
                'options': q['options'],
                'correct': q['correct'],
                'user': user_ans or 'No answer'
            })
    total = len(questions)
    wrong = total - correct_count
    percent = (correct_count / total * 100) if total > 0 else 0
    session['incorrect'] = incorrect
    html = f'<h1>Quiz Summary</h1><p>Correct: {correct_count}<br>Incorrect: {wrong}<br>Score: {percent:.2f}%</p>'
    html += '<a href="/review">Review Incorrect Answers</a><br><a href="/">Start New Quiz</a>'
    return render_template_string(html)

@app.route('/review')
def review():
    if 'incorrect' not in session:
        return redirect(url_for('summary'))
    incorrect = session.get('incorrect', [])
    html = '<h1>Incorrect Answers Review</h1>'
    for inc in incorrect:
        html += f'<h2>Question {inc["index"]}: {inc["question"]}</h2>'
        for i, opt in enumerate(inc['options']):
            label = chr(65 + i)
            color = 'red' if label == inc['user'] else 'green' if label == inc['correct'] else 'black'
            html += f'<p style="color: {color};">{label}) {opt}</p>'
        html += f'<p>Your answer: {inc["user"]}<br>Correct answer: {inc["correct"]}</p><hr>'
    html += '<a href="/summary">Back to Summary</a>'
    return render_template_string(html)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=True)
