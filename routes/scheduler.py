from flask import jsonify, make_response
from flask_restful import Resource, reqparse
from celery import shared_task
from configparser import ConfigParser
from datetime import datetime, timedelta

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import os
import json
import sqlite3
import smtplib
import requests

cd = os.path.dirname(os.path.abspath(__file__))
params = ConfigParser()
params.read(cd + '/../params.cfg')
alcala_url = params.get('Alcala', 'api_url')
client_id = params.get('Alcala', 'client_id')
client_secret = params.get('Alcala', 'client_secret')

parser = reqparse.RequestParser()
parser.add_argument('tuition_id', type=int, required=True, help='Tuition id is required')

DATABASE = '/alcala_microservice/alcala.db'
conn = sqlite3.connect(DATABASE)
cursor = conn.cursor()
res = cursor.execute("SELECT name FROM sqlite_master")
if res.fetchone() is None:
    cursor.execute("CREATE TABLE access_token(token, expiration_date)")
conn.close()

def send_message(email):
    email_sender = params.get('Mailing', 'email')
    email_sender_pswd = params.get('Mailing', 'password')
    msg = MIMEMultipart()
    msg['From'] = email_sender
    msg['To'] = email
    msg['Subject'] = 'Your tuition is ready'
    msg.attach(MIMEText('Go to https://classroom.clicformacion.es/campus'))
    try:
        server = smtplib.SMTP('smtp.office365.com', 587)
        server.starttls()
        server.ehlo()
        server.esmtp_features['auth'] = 'LOGIN DIGEST-MD5 PLAIN'
        server.login(email_sender, email_sender_pswd)
        text = msg.as_string()
        server.sendmail(email_sender, [email], text)
        server.quit()
    except (Exception, smtplib.SMTPException) as error:
        print(f'SMTP server connection error: {str(error)}')


@shared_task()
def check_tuition_status(tuition_id):
    # Request another access_token only if the current one is outdated
    access_token = ''
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    try:
        res = cursor.execute('SELECT token, expiration_date FROM access_token')
        data = res.fetchone()
        if data is not None:
            current_date = datetime.now()
            expiration_date = datetime.strptime(data[1]) - timedelta(minutes=5)
            if current_date < expiration_date:
                access_token = data[0]
            else:
                cursor.execute('DELETE from access_token WHERE token = ?', (data[0],))
                conn.commit()
    except Exception as error:
        conn.close()
        print(f'Error while reading access token from database: {str(error)}')
        return
    try:
        if not access_token:
            at_response = json.loads(requests.post(alcala_url + '/oauth/token', json={
                'grant_type': 'client_credentials',
                'client_id': client_id,
                'client_secret': client_secret
            }).text)
            access_token = at_response['access_token']
            expiration_date = datetime.now() + timedelta(seconds=int(at_response['expires_in']))
            cursor.execute('INSERT INTO access_token VALUES (?, ?)', (access_token, datetime.strftime(expiration_date)))
            conn.commit()
    except Exception as error:
        conn.close()
        print(f'Error while requesting access token: {str(error)}')
        return
    conn.close()
    # Getting tuition data
    try:
        tuition_data = json.loads(requests.get(alcala_url + f'/matriculas/{str(tuition_id)}', headers={
            'Authorization': f'Bearer {access_token}'
        }).text)
        print(json.dumps(tuition_data, indent=4))
    except Exception as error:
        print(f'Error while requesting tuition data: {str(error)}')
        return
    # TODO: number of retries
    if tuition_data['data']['estado_matricula'] == 'pendiente':
        exec_date = datetime.utcnow() + timedelta(minutes=5)
        check_tuition_status.apply_async((tuition_id,), eta=exec_date)
    else:
        send_message(tuition_data['data']['email'])

class Scheduler(Resource):
    def post(self):
        args = parser.parse_args()
        tuition_id = args['tuition_id']
        # Calling celery task
        exec_date = datetime.utcnow() + timedelta(minutes=2)
        try:
            check_tuition_status.apply_async((tuition_id,), eta=exec_date)
        except Exception as error:
            return make_response(jsonify({'message': f'Error while creating task: {str(error)}'}), 500)
        return jsonify({'message': 'Task scheduled'})
