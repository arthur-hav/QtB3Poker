#!/usr/bin/env python
import pika, sys, os


def main():
    rabbitmq_admin_password = os.getenv('RABBITMQ_ADMIN_PASSWORD', 'unsafe_for_production')
    credentials = pika.PlainCredentials('admin', rabbitmq_admin_password)
    connection = pika.BlockingConnection(pika.ConnectionParameters('localhost', virtual_host='game_start', credentials=credentials))
    channel = connection.channel()

    channel.queue_declare(queue='hello')
    channel.queue_bind('hello', 'public')

    def callback(ch, method, properties, body):
        print(" [x] Received %r" % body)

    channel.basic_consume(queue='hello', on_message_callback=callback, auto_ack=True)

    print(' [*] Waiting for messages. To exit press CTRL+C')
    channel.start_consuming()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Interrupted')
        try:
            sys.exit(0)
        except SystemExit:
            os._exit(0)