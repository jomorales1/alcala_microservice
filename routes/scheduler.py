from flask import jsonify, make_response
from flask_restful import Resource, reqparse
from celery import shared_task
from configparser import ConfigParser
from datetime import datetime, timedelta

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

import os
import json
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
    # TODO: request another access_token only if the current one is outdated
    try:
        access_token = json.loads(requests.post(alcala_url + '/oauth/token', json={
            'grant_type': 'client_credentials',
            'client_id': client_id,
            'client_secret': client_secret
        }).text)['access_token']
    except Exception as error:
        print(f'Error while requesting access token: {str(error)}')
        return
    # Getting tuition data
    try:
        tuition_data = json.loads(requests.get(alcala_url + f'/matriculas/{str(tuition_id)}', headers={
            'Authorization': f'Bearer {access_token}'
        }))
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
