from flask import jsonify, make_response
from flask_restful import Resource, reqparse
from celery import shared_task
from configparser import ConfigParser
from datetime import datetime, timedelta

from email import encoders
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart

import os
import json
import sqlite3
import smtplib
import requests

# Reading params file
cd = os.path.dirname(os.path.abspath(__file__))
params = ConfigParser()
params.read(cd + '/../params.cfg')
alcala_url = params.get('Alcala', 'api_url')
client_id = params.get('Alcala', 'client_id')
client_secret = params.get('Alcala', 'client_secret')

# Setting tuition_id to be required
parser = reqparse.RequestParser()
parser.add_argument('tuition_id', type=int, required=True, help='Tuition id is required')
parser.add_argument('course_id', type=int, required=True, help='Course id is required')

# Defining constants
MAX_RETRIES = int(params.get('Alcala', 'max_retries'))
DATABASE = cd + '/../alcala.db'
LOGS_PATH = cd + '/../logs'

# Setting up database
try:
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    res = cursor.execute("SELECT name FROM sqlite_master")
    if res.fetchone() is None:
        cursor.execute("CREATE TABLE access_token(token, expiration_date)")
    conn.close()
except Exception as error:
    print(f'Error while creating database: {str(error)}')

def send_message(course_id, email, username, password, name, lastname):
    """
        Send email to the user identified with `email`.

        To create the message a HTML template is used, which contains user credentials to access the course.
    """
    email_sender = params.get('Mailing', 'email')
    email_sender_pswd = params.get('Mailing', 'password')
    msg = MIMEMultipart()
    msg['From'] = email_sender
    msg['To'] = email
    msg['Subject'] = 'Your course is ready!'
    with open(cd + '/../static/html/enrollment_message.html', 'r', encoding='utf-8') as html_file:
        with open(cd + '/../static/data.json', 'r', encoding='utf-8') as json_file:
            data = json.loads(json_file.read())
            course_title = data[str(course_id)]['title']
        content = html_file.read()
        content = content.replace('{{course_title}}', course_title)
        content = content.replace('{{course_image_url}}', f'https://www.compumed.edu/files/123-{str(course_id)}-image/course{str(course_id)}.jpg')
        content = content.replace('{{username}}', username)
        content = content.replace('{{password}}', password)
        content = content.replace('{{name}}', name)
        content = content.replace('{{lastname}}', lastname)
        content = content.replace('{{moodle_url}}', params.get('Alcala', 'moodle_url'))
        msg.attach(MIMEText(content, 'html'))
    try:
        server = smtplib.SMTP('smtp.office365.com', 587)
        server.starttls()
        server.ehlo()
        server.esmtp_features['auth'] = 'LOGIN DIGEST-MD5 PLAIN'
        server.login(email_sender, email_sender_pswd)
        text = msg.as_string()
        server.sendmail(email_sender, [email], text)
        server.quit()
        print('Email sent')
    except (Exception, smtplib.SMTPException) as error:
        print(f'SMTP server connection error: {str(error)}')

def notify_admin(tuition_id):
    """
        Notify admin when tuition is not approved after MAX_RETRIES attempts.
    """
    admin_email = params.get('Mailing', 'admin_email')
    email_sender = params.get('Mailing', 'email')
    email_sender_pswd = params.get('Mailing', 'password')
    msg = MIMEMultipart()
    msg['From'] = email_sender
    msg['To'] = admin_email
    msg['Subject'] = 'Course enrollment failed'
    with open(cd + '/../static/html/admin_notification.html', 'r', encoding='utf-8') as html_file:
        content = html_file.read()
        content = content.replace('{{tuition_id}}', str(tuition_id))
        msg.attach(MIMEText(content, 'html'))
    # Include log file
    attachment_location = LOGS_PATH + f'/{str(tuition_id)}.log'
    filename = os.path.basename(attachment_location)
    attachment = open(attachment_location, 'rb')
    part = MIMEBase('application', 'octet-stream')
    part.set_payload(attachment.read())
    encoders.encode_base64(part)
    part.add_header('Content-Disposition', f'attachment; filename= {filename}')
    msg.attach(part)
    # Send message
    try:
        server = smtplib.SMTP('smtp.office365.com', 587)
        server.starttls()
        server.ehlo()
        server.esmtp_features['auth'] = 'LOGIN DIGEST-MD5 PLAIN'
        server.login(email_sender, email_sender_pswd)
        text = msg.as_string()
        server.sendmail(email_sender, [admin_email], text)
        server.quit()
        print('Notification sent')
    except (Exception, smtplib.SMTPException) as error:
        print(f'SMTP server connection error: {str(error)}')

@shared_task()
def check_tuition_status(tuition_id, course_id, prev_attempts):
    """
        Celery task to check tuition status.
        1. Request access token if there is not in the SQLite database.
        2. Request tuition data.
        3. If tuition is not approved schedule next task, otherwise notify user.

        If after MAX_RETRIES attempts tuition status is `pending` stop scheduling
        tasks and notify admin.
    """
    with open(LOGS_PATH + f'/{str(tuition_id)}.log', 'a+', encoding='utf-8') as log_file:
        if prev_attempts == 0:
            log_file.write(f'Task #{str(prev_attempts + 1)}\n')
        else:
            log_file.write(f'\nTask #{str(prev_attempts + 1)}\n')
        log_file.write(f'Date: {datetime.strftime(datetime.utcnow(), "%Y-%m-%dT%H:%M:%S")}\n')
        # Check if there is a valid access_token in the SQLite database
        access_token = ''
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        try:
            res = cursor.execute('SELECT token, expiration_date FROM access_token')
            data = res.fetchone()
            if data is not None:
                current_date = datetime.now()
                expiration_date = datetime.strptime(data[1], '%Y-%m-%dT%H:%M:%S') - timedelta(minutes=5)
                if current_date < expiration_date:
                    access_token = data[0]
                else:
                    cursor.execute('DELETE from access_token WHERE token = ?', (data[0],))
                    conn.commit()
        except Exception as error:
            conn.close()
            print(f'Error while reading access token from database (tuition_id = {str(tuition_id)}): {str(error)}')
        # Request access_token if not found previously
        if not access_token:
            try:
                at_request = requests.post(alcala_url + '/oauth/token', json={
                    'grant_type': 'client_credentials',
                    'client_id': client_id,
                    'client_secret': client_secret
                })
                log_file.write(f'Access token response: [{str(at_request.status_code)}]\n{json.dumps(json.loads(at_request.text), indent=4)}\n')
                # If request failed shedule next task
                if not at_request.ok:
                    conn.close()
                    print(f'Error while requesting access_token: [{str(at_request.status_code)}] {str(at_request.text)}')
                    # Check if max attempts were reached
                    if prev_attempts < MAX_RETRIES - 1:
                        print('Scheduling next task...')
                        exec_date = datetime.utcnow() + timedelta(minutes=3)
                        check_tuition_status.apply_async((tuition_id, course_id, prev_attempts + 1), eta=exec_date)
                    else:
                        print(f'Max retries exceeded ({str(prev_attempts + 1)}) with tuition_id {str(tuition_id)}')
                        notify_admin(tuition_id)
                    return
                # Process response and save new access_token
                at_response = json.loads(at_request.text)
                access_token = at_response['access_token']
                expiration_date = datetime.now() + timedelta(seconds=int(at_response['expires_in']))
                cursor.execute('INSERT INTO access_token VALUES (?, ?)', (access_token, datetime.strftime(expiration_date, '%Y-%m-%dT%H:%M:%S')))
                conn.commit()
            except Exception as error:
                conn.close()
                print(f'Error while requesting access token (tuition_id = {str(tuition_id)}): {str(error)}')
                log_file.write(f'Error while requesting access token: {str(error)}\n')
                notify_admin(tuition_id)
                return
        # Request tuition data
        try:
            tuition_request = requests.get(alcala_url + f'/matriculas/{str(tuition_id)}', headers={
                'Authorization': f'Bearer {access_token}'
            })
            log_file.write(f'Tuition response: [{str(tuition_request.status_code)}]\n{json.dumps(json.loads(tuition_request.text), indent=4)}\n')
            # If status code is 404 not found, stop scheduling tasks
            if tuition_request.status_code == 404:
                conn.close()
                print(f'Tuition not found. Next task will not be scheduled')
                return
            # If request failed shedule next task
            if not tuition_request.ok:
                if at_request.status_code == 401:
                    cursor.execute('DELETE from access_token WHERE token = ?', (access_token,))
                    conn.commit()
                print(f'Error while requesting tuition data: [{str(tuition_request.status_code)}] {str(tuition_request.text)}')
                # Check is max attempts were reached
                if prev_attempts < MAX_RETRIES - 1:
                    print('Scheduling next task...')
                    exec_date = datetime.utcnow() + timedelta(minutes=3)
                    check_tuition_status.apply_async((tuition_id, course_id, prev_attempts + 1), eta=exec_date)
                else:
                    print(f'Max retries exceeded ({str(prev_attempts + 1)}) with tuition_id {str(tuition_id)}')
                    notify_admin(tuition_id)
                conn.close()
                return
            # Process response
            tuition_data = json.loads(tuition_request.text)
            print(json.dumps(tuition_data, indent=4))
        except Exception as error:
            conn.close()
            print(f'Error while requesting tuition data (tuition_id = {str(tuition_id)}): {str(error)}')
            log_file.write(f'Error while requesting tuition data: {str(error)}\n')
            notify_admin(tuition_id)
            return
        conn.close()
        # Check tuition status
        if tuition_data['data']['estado_matricula'] == 'pendiente':
            # Check is max attempts were reached
            if prev_attempts < MAX_RETRIES - 1:
                print('Scheduling next task...')
                exec_date = datetime.utcnow() + timedelta(minutes=3)
                check_tuition_status.apply_async((tuition_id, course_id, prev_attempts + 1), eta=exec_date)
            else:
                print(f'Max retries exceeded ({str(prev_attempts + 1)}) with tuition_id {str(tuition_id)}')
                notify_admin(tuition_id)
        else:
            # Send email to user
            send_message(course_id, tuition_data['data']['email'], tuition_data['data']['usuario'], tuition_data['data']['password'], tuition_data['data']['nombre'], tuition_data['data']['apellidos'])

class Scheduler(Resource):
    def post(self):
        args = parser.parse_args()
        tuition_id = args['tuition_id']
        course_id = args['course_id']
        # Creating log file
        if not os.path.exists(LOGS_PATH):
            os.mkdir(LOGS_PATH)
        with open(LOGS_PATH + f'/{str(tuition_id)}.log', 'w', encoding='utf-8') as log_file:
            log_file.write(f'Request date: {datetime.strftime(datetime.utcnow(), "%Y-%m-%dT%H:%M:%S")}\n')
        # Calling celery task
        exec_date = datetime.utcnow() + timedelta(minutes=1)
        try:
            check_tuition_status.apply_async((tuition_id, course_id, 0), eta=exec_date)
        except Exception as error:
            return make_response(jsonify({'message': f'Error while creating task: {str(error)}'}), 500)
        return jsonify({'message': 'Task scheduled'})
