#!/usr/bin/env python3

# Owinec - Open Windows Event Collector
# Copyright (C) 2020, Lorenz Stechauner
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ssl
import ipaddress
import logging
from socketserver import ThreadingMixIn
import threading
import wsman
import xml.etree.ElementTree as ET

WSMAN_PORT_HTTP = 5985
WSMAN_PORT_HTTPS = 5986

ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
# ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.load_cert_chain('/root/server-cert.pem', '/root/server-key.pem')


formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
def setup_logger(name, log_file, level=logging.INFO):
    """To setup as many loggers as you want"""

    handler = logging.FileHandler(log_file)        
    handler.setFormatter(formatter)

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.addHandler(handler)

    return logger

event_logger = setup_logger("event-logger", "event.log")

class SoapHandler(BaseHTTPRequestHandler):
    server_version = 'owinec/1.0'

    def parse_request(self):
        threading.current_thread().setName(f'{self.client_address[0]}:{self.client_address[1]}')
        return super().parse_request()

    def do_GET(self):
        logger.info(f'GET {self.path} from {self.address_string()}, invalid method')
        self.send_response(HTTPStatus(HTTPStatus.METHOD_NOT_ALLOWED))
        self.end_headers()
        self.wfile.write(b'Method Not Allowed')

    def do_PUT(self):
        logger.info(f'PUT {self.path} from {self.address_string()}, invalid method')
        self.send_response(HTTPStatus(HTTPStatus.METHOD_NOT_ALLOWED))
        self.end_headers()
        self.wfile.write(b'Method Not Allowed')

    def do_POST(self):
        logger.info(f'POST {self.path} from {self.address_string()}')

        if isinstance(self.connection, ssl.SSLSocket):
            # Certificate Authentication
            # TODO check certificate
            pass
        else:
            # Other Authentication Protocols are not supported
            auth = self.headers['Authorization'] if 'Authorization' in self.headers else None
            logger.warning(f'401 Unauthorized - Unsupported authentication protocol: {auth}')
            self.send_response(HTTPStatus(HTTPStatus.UNAUTHORIZED))
            self.send_header('WWW-Authenticate', 'http://schemas.dmtf.org/wbem/wsman/1/wsman/secprofile/https/mutual')
            self.end_headers()
            self.wfile.write(b'Unauthorized - Unsupported authentication protocol - Use https instead')
            return

        content_length = int(self.headers['Content-Length']) if 'Content-Length' in self.headers else 0
        content_type = (self.headers['Content-Type'] or '').split(';')
        charset = None
        if len(content_type) > 1 and content_type[1].strip().startswith('charset='):
            charset = content_type[1].strip()[8:]

        if content_length == 0:
            logger.warning(f'{self.path} - {HTTPStatus.LENGTH_REQUIRED} Length Required')
            self.send_response(HTTPStatus(HTTPStatus.LENGTH_REQUIRED))
            self.send_header('WWW-Authenticate', 'http://schemas.dmtf.org/wbem/wsman/1/wsman/secprofile/https/mutual')
            self.send_header('Content-Length', '0')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(b'Length Required - This request requires a payload')
            return

        payload = self.rfile.read(content_length)
        if charset == 'UTF-16':
            text = payload.decode('utf16')
        else:
            text = payload.decode('utf8')

        logger.info(f"input data {text}")

        envelope = wsman.Envelope.load(ET.fromstring(text))
        logger.debug(f'Action={envelope.action}, ResourceURI={envelope.resource_uri}')
        for error in envelope.errors:
            logger.error(f'From {error["machine"]} (code {error["code"]}): {error["text"]}')
            if error['code'] == 5004 or error['code'] == 1818:
                logger.error(f'Tip: Verify that \'NT Authority\\Network Service\' is a member of the '
                             f'\'Event Log Readers\' group on the source computer.')

        response = None
        if envelope.action == wsman.ACTION_END and envelope.resource_uri == wsman.RESOURCE_URI_FULL_DUPLEX:
            response = ''
        elif envelope.action == wsman.ACTION_ENUMERATE and envelope.resource_uri == wsman.RESOURCE_URI_SUBSCRIPTION:
            # Initial request from client
            response = self.do_enumerate(envelope)
        elif envelope.action == wsman.ACTION_HEARTBEAT and envelope.resource_uri is None:
            response = self.do_heartbeat(envelope)
        elif envelope.action == wsman.ACTION_END_SUBSCRIPTION and envelope.resource_uri is None:
            response = ''
        elif envelope.action == wsman.ACTION_EVENTS and envelope.resource_uri is None:
            response = self.do_events(envelope)
        else:
            logger.info(f'{self.path} - {envelope.action}/{envelope.resource_uri} - 501 Not implemented')
            logger.warning(f'Envelope not implemented: {text}')
            self.send_response(HTTPStatus(HTTPStatus.NOT_IMPLEMENTED))
            self.send_header('WWW-Authenticate', 'http://schemas.dmtf.org/wbem/wsman/1/wsman/secprofile/https/mutual')
            self.end_headers()
            self.wfile.write(b'Not Implemented')
            return

        logger.info(f"output data: {response}")
        response = response.encode('utf8')
        logger.info(f'{self.path} - {envelope.action}/{envelope.resource_uri}')
        self.send_response(HTTPStatus(HTTPStatus.OK))
        self.send_header('WWW-Authenticate', 'http://schemas.dmtf.org/wbem/wsman/1/wsman/secprofile/https/mutual')
        self.send_header('Content-Type', 'application/soap+xml;charset=UTF-8')
        self.send_header('Content-Length', str(len(response)))
        self.send_header('Connection', 'Keep-Alive')
        self.end_headers()
        self.wfile.write(response)

    def send_response(self, code: HTTPStatus, message=None):
        return super().send_response(code, message=message)

    def log_message(self, format, *args):
        return

    def do_enumerate(self, envelope: wsman.EnumerateSubscriptionEnvelope) -> str:
        pass

    def do_heartbeat(self, envelope: wsman.HeartbeatEnvelope) -> str:
        pass

    def do_events(self, envelope: wsman.EventsEnvelope) -> str:
        pass


class WSManHandler(SoapHandler):
    def do_enumerate(self, envelope: wsman.EnumerateSubscriptionEnvelope) -> str:
        subscription = wsman.SubscriptionEnvelope(
            'subscription2', 'Test Subscription 2', 'https://10.0.1.170:5986/owinec/subscriptions/s2',
            [('Security', '*[System[(Level=1 or Level=2 or Level=3 or Level=4 or Level=0 or Level=5)]]'),
             ('System', '*[System[(Level=1 or Level=2 or Level=3 or Level=4 or Level=0 or Level=5)]]')],
            ['663FC825E1657B6361DB624E549EF8EA839684A0'] # Replace by CA thumbprint
        )
        subscription.bookmarks = False
        subscription.read_existing_events = True
        subscription.content_format = 'RenderedText'
        subscription.max_time = 0.0
        subscription.connection_retries = 60
        subscription.connection_retries_wait = 10.0
        subscription.heartbeat_sec = 60.0
        subscription.max_envelope_size = 10 * 1024 * 1024
        response = wsman.EnumerateResponseEnvelope(subscription, envelope.operation_id, relates_to=envelope.id)
        response.to = envelope.reply_to
        return response.dump()

    def do_heartbeat(self, envelope: wsman.HeartbeatEnvelope) -> str:
        response = wsman.AckEnvelope(envelope.id, envelope.operation_id)
        return response.dump()

    def do_events(self, envelope: wsman.EventsEnvelope) -> str:
        for e in envelope.events:
            event_logger.info(e.encode('utf8'))
            # print(e.encode('utf8'))
        response = wsman.AckEnvelope(envelope.id, envelope.operation_id)
        return response.dump()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='owinec - Open Windows Event Collector')
    parser.add_argument('-p', '--protocol', type=str, default='https', choices=['http', 'https'],
                        help='The protocol to use, default is https')
    parser.add_argument('-l', '--listen-address', type=ipaddress.ip_address, default='0.0.0.0',
                        help='The ip address to bind and listen to, default is 0.0.0.0')
    parser.add_argument('-P', '--port', type=int,
                        help='The tcp port to bind and listen to, default for http is 5985, for https is 5986')
    parser.add_argument('--cert', type=argparse.FileType('r'),
                        help='The certificate file to use for https')
    parser.add_argument('--key', type=argparse.FileType('r'),
                        help='The private key file to use for https')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Be verbose')
    args = parser.parse_args()

    logger = logging.getLogger('owinec')
    logger.setLevel(logging.DEBUG)

    cmd_handler = logging.StreamHandler()
    cmd_handler.setFormatter(logging.Formatter('[%(asctime)s][%(threadName)s][%(levelname)s] %(message)s'))
    logger.addHandler(cmd_handler)

    if args.verbose:
        cmd_handler.setLevel(logging.DEBUG)
    else:
        cmd_handler.setLevel(logging.INFO)

    logger.debug('Starting owinec...')
    logger.debug(f'Command line arguments: {args}')

    if args.protocol in ('http', 'https'):
        logger.debug(f'Using protocol {args.protocol}')
        bind_address = str(args.listen_address)
        bind_port = args.port or WSMAN_PORT_HTTP if args.protocol == 'http' else WSMAN_PORT_HTTPS

        httpd = ThreadingHTTPServer((bind_address, bind_port), WSManHandler)

        if args.protocol == 'https':
            if not args.cert or not args.key:
                raise FileNotFoundError('certificate and private key have to be specified when using https')
            httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        else:
            # TODO implement http client handling
            logger.critical('Http is not supported and not secure - use https instead')
            exit(1)

        logger.info(f'Listening on {args.protocol}://{bind_address}:{bind_port}/')

        httpd.serve_forever()
    else:
        raise NotImplementedError()
