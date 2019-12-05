
# Copyright 2017 Derek Weitzel
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import datetime
import json
import tarfile
import dateutil.parser
import pika


class UnArchiver(object):

    def __init__(self, url, exchange, start_date=None, end_date=None, sleep=0):
        self.url = url
        self.exchange = exchange
        self.start_date = start_date
        self.end_date = end_date
        self.sleep = sleep
        pass

    def createConnection(self):
        self.parameters = pika.URLParameters(self.url)
        self._conn = pika.adapters.blocking_connection.BlockingConnection(self.parameters)

        self._chan = self._conn.channel()

    def sendRecord(self, record):
        self._chan.basic_publish(exchange=self.exchange, routing_key='', body=record)

    def dateFilter(self, record):
        '''Filter records by date, returning false if record should be ignored'''
        if not self.start_date and not self.end_date:
            return True

        json_record = json.loads(record)
        try:
            dt = None
            # perfSonar: meta.ts_start = 1575331130
            dt = datetime.datetime.utcfromtimestamp(json_record['meta']['ts_start'])
            # GRACC: EndTime = "2019-12-02T22:38:58Z"
            dt = datetime.datetime.strptime(json_record['EndTime'], '%Y-%m-%dT%H:%M:%SZ')
        except KeyError:
            pass
        if dt is None:
            # No timestamp found; allow the record
            return True

        if self.start_date and dt < self.start_date:
            return False
        if self.end_date and dt >= self.end_date:
            return False
        return True

    def parseTarFile(self, tar_file, start=0):
        tf = tarfile.open(tar_file, mode='r')

        counter = 0
        sent_counter = 0
        # For each file in the tar file:
        for member in tf:
            if counter < start:
                counter += 1
                if (counter % 10000) == 0:
                    self._conn.process_data_events()
                    print("Skipping {} records".format(counter))
                    tf.members = []
                continue
            f = tf.extractfile(member)
            record = f.read()
            if self.dateFilter(record):
                self.sendRecord(record)
                sent_counter += 1

                # Sleep between batches
                if self.sleep and (sent_counter % 10000) == 0:
                    self._conn.sleep(self.sleep)

            counter += 1
            if (counter % 10000) == 0:
                self._conn.process_data_events()
                print("Processed {} records and sent {} records".format(counter, sent_counter))
                tf.members = []

        tf.close()


class PerfSonarUnArchiver(UnArchiver):
    """
    Subclass of the UnArchiver in order to send PS data
    """
    def __init__(self, url, exchange, start_date=None, end_date=None, sleep=0):
        super(PerfSonarUnArchiver, self).__init__(url, exchange, start_date, end_date, sleep)

    def sendRecord(self, record):
        # Parse the json record, looking for the "event-type"
        json_record = json.loads(record)
        event_type = json_record['meta']['event-type']

        # Prepend the "perfsonar.raw." to the event-type
        routing_key = "perfsonar.raw." + event_type

        self._chan.basic_publish(exchange=self.exchange, routing_key=routing_key, body=record)


def main():
    # Parse arguments
    parser = argparse.ArgumentParser(description="GRACC UnArchiver")

    parser.add_argument("rabbiturl", help="Rabbit URL Parameters")
    parser.add_argument("exchange", help="Exchange to send records")
    parser.add_argument("tarfile", nargs='+', help="Tar Files to parse and send")
    parser.add_argument("-p", "--psdata", help="Unarchive perfsonar data", action='store_true')
    parser.add_argument("-s", "--start", help="Record number to start sending", type=int, default=0)
    parser.add_argument("--start-date", help="Select records on or after the specified date (UTC ISO-8601)", type=dateutil.parser.parse)
    parser.add_argument("--end-date", help="Select records before the specified date (UTC ISO-8601)", type=dateutil.parser.parse)
    parser.add_argument("--sleep", help="Seconds to sleep between 10k record batches", type=int, default=7)

    args = parser.parse_args()

    if args.psdata:
        unarchive = PerfSonarUnArchiver(args.rabbiturl, args.exchange, args.start_date, args.end_date, args.sleep)
    else:
        unarchive = UnArchiver(args.rabbiturl, args.exchange, args.start_date, args.end_date, args.sleep)
    unarchive.createConnection()

    for tar_file in args.tarfile:
        print("Parsing %s" % tar_file)
        unarchive.parseTarFile(tar_file, start=args.start)




if __name__ == '__main__':
    main()
