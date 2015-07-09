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
        split = event.arguments[0].rstrip().split(" ")

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
            command = args[1].strip()
            match = re.match(r"^(?P<channel>[#\w]+) (?P<date>[\d-]+) (?P<search>.+)$", command, flags=re.UNICODE)
            if match:
                self.perform_grep_request(event, match.group("channel").lower(), match.group("date"), match.group("search"))
                return

            match = re.match(r"^stalk (?P<channel>[#\w]+) (?P<date>[\d-]+) (?P<search>.+)$", command, flags=re.UNICODE)
            if match:
                self.perform_stalk_request(event, match.group("channel").lower(), match.group("date"), match.group("search"))
                return
            
            self.connection.privmsg(event.target, "%s: I'm sorry, I don't know what you mean" % (event.source.nick))

    def on_privnotice(self, connection, event):
        if event.source == 'NickServ!NickServ@services.' and 'You are now identified' in event.arguments[0]:
            for channel in self.config['join']:
                connection.join(channel)

    def split_mask(self, mask):
        nick_mask = mask.split('!', 1)
        if len(nick_mask) == 2:
            ident_mask = nick_mask[1].split('@', 1)
            if len(ident_mask) == 1:
                ident_mask = (ident_mask[0], "")
        else:
            ident_mask = ("", "")
        return (nick_mask[0], ident_mask[0], ident_mask[1])

    def join_mask(self, mask):
        return u"%s!%s@%s" % mask

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

    def test_nick_equality(self, nick_a, nick_b):
        if nick_a.lower() == nick_b.lower():
            return "is identical to"

        pattern = re.compile(r"^[\W_]?(?:\[.+?\])?(?P<nick>\w+?)(?:[\W_]\w+|away|gone|bnc|off|z+|sleep|afk|work)?[\W_]*$", flags=re.IGNORECASE)
        match_a = pattern.match(nick_a)
        match_b = pattern.match(nick_b)

        if match_a and match_b:
            if match_a.group("nick").lower() == match_b.group("nick").lower():
                return "is similar to"
        
        return None

    def test_host_equality(self, host_a, host_b):
        if host_a.lower() == host_b.lower():
            return "is identical to"
        pattern = re.compile(r"\b(?:[0-9]{1,3}[\.-]){3}[0-9]{1,3}\b")
        match_a = pattern.match(host_a)
        match_b = pattern.match(host_b)

        if match_a and match_b:
            if match_a.group(0) == match_b.group(0):
                return "has same ip as"
        
        return None

    def find_similar_users(self, files, search_nicks, search_idents, search_hosts, nicks=None, idents=None, hosts=None, last_seen=None):
        if not nicks:
            nicks = collections.OrderedDict()
        if not idents:
            idents = collections.OrderedDict()
        if not hosts:
            hosts = collections.OrderedDict()
        if not last_seen:
            last_seen = collections.OrderedDict()

        for file in sorted(glob.iglob(files)):
            date = file[-len(".log") - len("yyyymmdd"):-len(".log")]
            for line in open(file, 'r'):
                line = unicode(line.strip(), errors='replace')
                for type in self.config['stalker_formats']:
                    match = re.match(self.config['format'][type], line)
                    if match:
                        nick = match.group("nick")
                        seen = False

                        if "new_nick" in match.groupdict():
                            new_nick = match.group("new_nick")
                            ident = None
                            host = None
                        else:
                            new_nick = None
                            ident = match.group("ident")
                            host = match.group("host")

                        for search_nick in search_nicks:
                            if new_nick:
                                if new_nick not in nicks:
                                    new_nick_equality = self.test_nick_equality(search_nick, new_nick)
                                    if new_nick_equality:
                                        nicks[new_nick] = u"Changed nick from %s to %s" % (nick, new_nick)
                                        seen = True
                                elif nick not in nicks:
                                    nick_equality = self.test_nick_equality(search_nick, nick)
                                    if nick_equality:
                                        nicks[nick] = u"Changed nick from %s to %s" % (nick, new_nick)
                                        seen = True
                            else:
                                if nick not in nicks:
                                    nick_equality = self.test_nick_equality(search_nick, nick)
                                    if nick_equality:
                                        nicks[nick] = u"Nick %s %s %s!%s@%s" % (search_nick, nick_equality, nick, ident, host)
                                if nick in nicks:
                                    seen = True
                                    if ident not in idents:
                                        idents[ident] = self.join_mask((nick, ident, host))
                                    if host not in hosts:
                                        hosts[host] = self.join_mask((nick, ident, host))

                        for search_ident in search_idents:
                            if ident and host:
                                if ident not in idents:
                                    if search_ident.lower() == ident.lower():
                                        seen = True
                                        idents[ident] = u"Ident %s is identical to %s!%s@%s" % (search_ident, nick, ident, host)

                        for search_host in search_hosts:
                            if ident and host:
                                if host not in hosts:
                                    host_equality = self.test_host_equality(search_host, host)
                                    if host_equality:
                                        hosts[host] = u"Host %s %s %s" % (search_host, host_equality, nick, ident, host)
                                if host in hosts:
                                    seen = True
                                    if nick not in nicks:
                                        nicks[nick] = self.join_mask((nick, ident, host))
                                    if ident not in idents:
                                        idents[ident] = self.join_mask((nick, ident, host))

                        if seen and ident and host:
                            last_seen[self.join_mask((nick, ident, host))] = u"%s-%s-%s %s" % (date[0:4], date[4:6], date[6:8], match.group("time"))

        return nicks, idents, hosts, last_seen

    def add_user_connection(self, results, user, reason=None):
        if user not in results:
            results[user] = set()
        if reason:
            results[user].add(reason)

    def create_log_line(self, line):
        type = "UNKNOWN"

        for key, pattern in self.config['format'].iteritems():
            if re.match(self.config['format'][key], line):
                type = key
                break

        return LogEntry(line, type)

    def create_gist(self, target, nick, message, content):
        try:
            r = requests.post(
                "https://api.github.com/gists",
                data=json.dumps({
                    "description": "",
                    "public": False,
                    "files": {
                        "results.txt": {
                            "content": content
                        }
                    }
                }),
                headers={
                    'Content-Type': 'application/json'
                }
            )
            if r.status_code == 201:
                self.connection.privmsg(target, '%s: %s (%s)' % (nick, r.json()['files']['results.txt']['raw_url'], message))
            else:
                self.connection.privmsg(target, '%s: Sorry... something went wrong. :( I got a HTTP %s: %s' % (nick, r.status_code, r.text))
        except Exception as exception:
            logger.exception(exception)
            self.connection.privmsg(target, '%s: Sorry... something went wrong. :( I got a %s' % (nick, exception.__class__.__name__))

    def perform_grep_request(self, event, channel, date, search):
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

            self.create_gist(
                event.target,
                event.source.nick,
                u"%d lines found - mostly of type %s" % (totalLines, max(linesByType, key=linesByType.get)),
                u"Showing %d log lines over %d days for search pattern: %s\n%s\n%s" % (totalLines, len(results), pattern.pattern, stats, gist)
            )
        else:
            self.connection.privmsg(event.target, "%s: Sorry, no results searching %s %s for %s" % (event.source.nick, channel, date, search))

    def perform_stalk_request(self, event, channel, date, search):
        if channel not in self.config['channels']:
            self.connection.privmsg(event.target, "%s: I'm sorry, I cannot let you grep %s" % (event.source.nick, channel))
            return

        search_nicks = set()
        search_idents = set()
        search_hosts = set()

        for part in search.split(' '):
            target = self.split_mask(part)
            if target[0] and target[0] != '*':
                search_nicks.add(target[0])
            if target[1] and target[1] != '*':
                search_nicks.add(target[1])
            if target[2] and target[2] != '*':
                search_nicks.add(target[2])

        date = date.replace("-", "")
        date = date.ljust(8, "?")

        if len(date) > 8:
            self.connection.privmsg(event.target, "%s: I'm sorry, I don't know when %s is" % (event.source.nick, date))
            return

        nicks, idents, hosts, last_seen = self.find_similar_users("%s%s_%s.log" % (self.config['logs'], channel, date), search_nicks, search_idents, search_hosts)
        nicks, idents, hosts, last_seen = self.find_similar_users("%s%s_%s.log" % (self.config['logs'], channel, date), nicks.keys(), idents.keys(), hosts.keys(), nicks, idents, hosts, last_seen)

        if nicks or idents or hosts or last_seen:
            gist = u"Showing %d nick(s) / %d ident(s) / %d host(s) for %s" % (len(nicks), len(idents), len(hosts), search)

            gist += u"\n\nNicks:\n"
            for nick, reason in nicks.iteritems():
                if reason:
                    gist += u"\t%s     (%s)\n" % (nick, reason)
                else:
                    gist += u"\t%s\n" % nick

            gist += u"\n\nIdents:\n"
            for ident, reason in idents.iteritems():
                if reason:
                    gist += u"\t%s     (%s)\n" % (ident, reason)
                else:
                    gist += u"\t%s\n" % ident

            gist += u"\n\nHosts:\n"
            for host, reason in hosts.iteritems():
                if reason:
                    gist += u"\t%s     (%s)\n" % (host, reason)
                else:
                    gist += u"\t%s\n" % host

            gist += u"\n\nFull User Masks:\n"
            for user, when in last_seen.iteritems():
                gist += u"\t%s     (%s)\n" % (user, when)

            self.create_gist(
                event.target,
                event.source.nick,
                u"%d nick(s) / %d ident(s) / %d host(s)" % (len(nicks), len(idents), len(hosts)),
                gist
            )
        else:
            self.connection.privmsg(event.target, "%s: Sorry, no results searching %s %s for %s" % (event.source.nick, channel, date, self.join_mask(target)))


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
