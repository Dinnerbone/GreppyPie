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
import collections

logger = logging.getLogger(__name__)
ServerConnection.buffer_class.errors = 'replace'

class LogEntry:
    def __init__(self, line, type):
        self.type = type
        self.line = line

    def __unicode__(self):
        return self.line

    def __str__(self):
        return unicode(self).encode('utf-8')

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

    def on_action(self, connection, event):
        target = event.target
        split = event.arguments[0].split(" ")

        if target == connection.get_nickname():
            target = NickMask(event.source).nick

        if len(split) == 2 and split[1] == connection.get_nickname():
            connection.action(target, u"%s %s <3" % (split[0], NickMask(event.source).nick))

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

    def grep_for_lines(self, files, pattern):
        results = collections.OrderedDict()

        for file in sorted(glob.iglob(files)):
            lines = []
            for line in open(file, 'r'):
                if re.search(pattern, line):
                    lines.append(self.create_log_line(unicode(line.strip(), errors='replace')))
            if lines:
                date = file[-len(".log") - len("yyyymmdd"):-len(".log")]
                results[date] = lines

        return results

    def create_log_line(self, line):
        type = "UNKNOWN"

        for key, pattern in self.config['format'].iteritems():
            if re.match(self.config['format'][key], line):
                type = key
                break

        return LogEntry(line, type)

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

            results = self.grep_for_lines("%s%s_%s.log" % (self.config['logs'], channel, date), pattern)
            totalLines = 0
            linesByType = collections.defaultdict(int)

            if results:
                gist = u""

                for date, lines in results.iteritems():
                    gist += u"----- %s-%s-%s -----\n%s\n\n" % (date[0:4], date[4:6], date[6:8], u"\n".join(unicode(line) for line in lines))
                    for line in lines:
                        totalLines += 1
                        linesByType[line.type] += 1

                stats = u""

                for type, count in sorted(linesByType.iteritems(), key=lambda item: -item[1]):
                    stats += u"%s: %d%% (%d lines)\n" % (type, float(count) / totalLines * 100, count)

                try:
                    r = requests.post(
                        "https://api.github.com/gists",
                        data=json.dumps({
                            "description": "",
                            "public": False,
                            "files": {
                                "results.txt": {
                                    "content": u"Showing %d log lines over %d days for search pattern: %s\n%s\n%s" % (totalLines, len(results), pattern.pattern, stats, gist)
                                }
                            }
                        }),
                        headers={
                            'Content-Type': 'application/json'
                        }
                    )
                    if r.status_code == 201:
                        self.connection.privmsg(event.target, '%s: %s (%d lines found - mostly of type %s)' % (event.source.nick, r.json()['files']['results.txt']['raw_url'], totalLines, max(linesByType, key=linesByType.get)))
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
