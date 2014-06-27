from irc.bot import SingleServerIRCBot
from irc.client import NickMask, ServerConnection
from datetime import datetime
import re
import logging
import yaml
import irc.strings
import glob
import requests
import json

logger = logging.getLogger(__name__)
ServerConnection.buffer_class.errors = 'replace'

class GreppyPieBot(SingleServerIRCBot):
    def __init__(self, config_file):
        self.config_file = config_file
        self.load_config()
        self.save_config()
        SingleServerIRCBot.__init__(
            self, server_list=[self.config['server']], nickname=self.config['nickname'], realname=self.config['realname'])

    def load_config(self):
        with open(self.config_file, 'r') as file:
            self.config = yaml.load(file)
            if not self.config['logs'].endswith("/"):
                self.config['logs'] = self.config['logs'] + "/"

    def save_config(self):
        with open(self.config_file, 'w') as file:
            yaml.dump(self.config, file, default_flow_style=False)

    def on_welcome(self, connection, event):
        for channel in self.config['join']:
            connection.join(channel)

    def on_pubmsg(self, connection, event):
        args = re.split(r"\b\S\s", event.arguments[0], 1, flags=re.UNICODE)
        if len(args) > 1 and irc.strings.lower(args[0]) == irc.strings.lower(self.connection.get_nickname()):
            self.perform_grep_request(event, args[1].strip())

    def on_privnotice(self, connection, event):
        if event.source == 'NickServ!NickServ@services.' and 'You are now identified' in event.arguments[0]:
            for channel in self.config['join']:
                connection.join(channel)

    def perform_grep_request(self, event, command):
        match = re.match(r"^(?P<channel>[#\w]+) (?P<date>[\d-]+) (?P<search>.+)$", command, flags=re.UNICODE)
        if match:
            channel = match.group("channel").lower()
            date = match.group("date")
            search = match.group("search")

            if channel not in self.config['channels']:
                self.connection.privmsg(event.target, "%s: I'm sorry, I cannot let you grep %s" % (event.source.nick, channel))
                return

            try:
                pattern = re.compile(search, re.IGNORECASE)
            except re.error as e:
                self.connection.privmsg(event.target, "%s: I'm sorry, but that is an invalid pattern (%s)" % (event.source.nick, e))
                return

            date = date.replace("-", "")
            date = date.ljust(8, "?")

            if len(date) > 8:
                self.connection.privmsg(event.target, "%s: I'm sorry, I don't know when %s is" % (event.source.nick, date))
                return

            results = u""
            totalLines = 0
            for file in glob.iglob("%s%s_%s.log" % (self.config['logs'], channel, date)):
                lines = []
                for line in open(file, 'r'):
                    if re.search(pattern, line):
                        lines.append(unicode(line.strip(), errors='replace'))
                        totalLines = totalLines + 1
                if lines:
                    date = file[len(self.config['logs']) + len(channel) + 1:-len(".log")]
                    results += u"----- %s-%s-%s -----\n%s\n\n" % (date[0:4], date[4:6], date[6:8], u"\n".join(lines))

            if results:
                try:
                    r = requests.post(
                        "https://api.github.com/gists",
                        data=json.dumps({
                            "description": "",
                            "public": False,
                            "files": {
                                "results.txt": {
                                    "content": results
                                }
                            }
                        }),
                        headers={
                            'Content-Type': 'application/json'
                        }
                    )
                    if r.status_code == 201:
                        self.connection.privmsg(event.target, '%s: %s (%d lines found)' % (event.source.nick, r.json()['files']['results.txt']['raw_url'], totalLines))
                    else:
                        self.connection.privmsg(event.target, '%s: Sorry... something went wrong. :( I got a HTTP %s: %s' % (event.source.nick, r.status_code, r.text))
                except Exception as exception:
                    logger.exception(exception)
                    self.connection.privmsg(event.target, '%s: Sorry... something went wrong. :( I got a %s' % (event.source.nick, exception.__class__.__name__))
            else:
                self.connection.privmsg(event.target, "%s: Sorry, no results searching %s %s for %s" % (event.source.nick, channel, date, search))
        else:
            self.connection.privmsg(event.target, "%s: I'm sorry, I don't know what you mean" % (event.source.nick))

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys
    if len(sys.argv) > 2 or len(sys.argv) > 1 and sys.argv[1] == '--help':
        print("Usage: greppypie.py [config.yml]")
        sys.exit(1)
    config = 'config.yml'
    if len(sys.argv) > 1:
        config = sys.argv[1]
    bot = GreppyPieBot(config)
    bot.start()
