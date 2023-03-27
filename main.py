from flask import Flask
from flask_restful import Api
from routes.scheduler import Scheduler
from celery import Celery

app = Flask(__name__)
app.config['CELERY_BROKER_URL'] = 'redis://localhost:6379'
# app.config['CELERY_RESULT_BACKEND'] = 'redis://localhost:6379'

api = Api(app)
api.add_resource(Scheduler, '/task/schedule')
celery = Celery(app.name, broker=app.config['CELERY_BROKER_URL'])
# celery.conf.update(app.config)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
