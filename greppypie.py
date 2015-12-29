from twisted.words.protocols import irc
from twisted.internet import reactor, protocol, defer, threads, task
from twisted.logger import Logger, textFileLogObserver, globalLogPublisher
import time, sys, yaml, re, glob, datetime, requests, json, random, time, os


class Victim:
    nick_pattern = re.compile(r"^[\W_]?(?:\[.+?\])?(?P<nick>\w+?)(?:[\W_]\w+|away|gone|bnc|off|z+|sleep|afk|work|\d+)?[\W_]*$", flags=re.IGNORECASE)

    def __init__(self, nick):
        self.nicks = {}
        self.hosts = {}
        self.idents = {}
        self.full_masks = {}
        self.base_nicks = set()
        self.add_nick(nick)

    def add_nick(self, nick, reason=None):
        if nick not in self.nicks:
            self.nicks[nick] = set()
            if reason:
                self.nicks[nick].add(reason)

            match = self.nick_pattern.match(nick)
            if match and len(match.group("nick")) > 3:
                self.base_nicks.add(match.group("nick").lower())
            else:
                self.base_nicks.add(nick.lower())

    def add_full_mask(self, nick, ident, host, date):
        mask = "%s!%s@%s" % (nick, ident, host)
        if mask in self.full_masks:
            entry = self.full_masks[mask]
            if date > entry['last_seen']:
                entry['last_seen'] = date
            if date < entry['first_seen']:
                entry['first_seen'] = date
        else:
            self.full_masks[mask] = {'first_seen': date, 'last_seen': date}
        self.add_host(host, mask)
        self.add_ident(ident, mask)
        self.add_nick(nick, mask)

    def add_host(self, host, reason=None):
        if host not in self.hosts:
            self.hosts[host] = set()
            if reason:
                self.hosts[host].add(reason)

    def add_ident(self, ident, reason=None):
        if ident not in self.idents:
            self.idents[ident] = set()
            if reason:
                self.idents[ident].add(reason)

    def is_similar(self, other):
        if other is self:
            return "identical"
        for base in self.base_nicks:
            if "guest" not in base and base in other.base_nicks:
                return "common base nick %s" % base
        for host in self.hosts:
            if host != "gateway/web/irccloud.com/session" and host in other.hosts:
                return "common host %s" % host
        
        return False

    def merge(self, other, reason=None):
        if other is self:
            return
        self.base_nicks |= other.base_nicks
        for nick in other.nicks:
            if nick in self.nicks:
                self.nicks[nick] |= other.nicks[nick]
            else:
                self.nicks[nick] = other.nicks[nick]
            if reason:
                self.nicks[nick].add(reason)
        for host in other.hosts:
            if host in self.hosts:
                self.hosts[host] |= other.hosts[host]
            else:
                self.hosts[host] = other.hosts[host]
            if reason:
                self.hosts[host].add(reason)
        for ident in other.idents:
            if ident in self.idents:
                self.idents[ident] |= other.idents[ident]
            else:
                self.idents[ident] = other.idents[ident]
            if reason:
                self.idents[ident].add(reason)

        for mask, other_entry in other.full_masks.iteritems():
            if mask in self.full_masks:
                entry = self.full_masks[mask]
                if other_entry['first_seen'] < entry['first_seen']:
                    entry['first_seen'] = other_entry['first_seen']
                if other_entry['last_seen'] > entry['last_seen']:
                    entry['last_seen'] = other_entry['last_seen']
            else:
                self.full_masks[mask] = other_entry

    def __str__(self):
        return self.nicks[0] if self.nicks else None


class TextUploader:
    log = Logger()

    def upload_text(self, content):
        return threads.deferToThread(self._upload_text, content)

    def _upload_text(self, content):
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
        return str(r.json()['files']['results.txt']['raw_url'])


class MessageHistory:
    log = Logger()

    def __init__(self, directory, filename_format, formats):
        self.directory = directory
        self.filename_format = filename_format
        self.formats = formats

    def _create_log_line(self, line):
        for key, pattern in self.formats.iteritems():
            match = re.match(self.formats[key], line)
            if match:
                return (key, line, match.groupdict())

        return None

    def _grep_lines_in_file(self, logfile, pattern):
        path, date = logfile
        lines = []
        for line in open(path, 'r'):
            if re.search(pattern, line):
                entry = self._create_log_line(line.strip())
                if entry:
                    lines.append(entry)
        return date, lines

    def _find_victims_in_file(self, logfile):
        path, date = logfile
        victims = {}
        for line in open(path, 'r'):
            entry = self._create_log_line(line.strip())
            if entry:
                type, line, parts = entry
                for key in ["nick", "kicker_nick", "new_nick"]:
                    if key in parts:
                        nick = parts[key]
                        if nick not in victims:
                            victims[nick] = Victim(nick)
                if "new_nick" in parts and "nick" in parts:
                    nick = parts["nick"]
                    new_nick = parts["new_nick"]
                    if nick in victims:
                        if new_nick in victims:
                            old = victims[nick]
                            victim = victims[new_nick]
                            victim.merge(old, "changed nick from %s to %s" % (nick, new_nick))
                            victims[nick] = victim
                        else:
                            victim = victims[nick]
                            victim.add_nick(new_nick, "changed nick from %s to %s" % (nick, new_nick))
                            victims[new_nick] = victim
                    else:
                        if new_nick in victims:
                            victim = victims[new_nick]
                            victim.add_nick(nick, "changed nick from %s to %s" % (nick, new_nick))
                            victims[nick] = victim
                        else:
                            victim = Victim(nick)
                            victim.add_nick(new_nick, "changed nick from %s to %s" % (nick, new_nick))
                            victims[nick] = victim
                            victims[new_nick] = victim
                if "host" in parts and "ident" in parts and "nick" in parts:
                    nick = parts["nick"]
                    ident = parts["ident"]
                    host = parts["host"]
                    if nick in victims:
                        victims[nick].add_full_mask(nick, ident, host, date)
                    else:
                        victim = Victim(nick)
                        victim.add_full_mask(nick, ident, host, date)
                        victims[nick] = victim

        return date, victims

    def _find_files(self, min_date, channel):
        result = []
        for path in sorted(glob.iglob(self.directory.format(channel=channel))):
            filename = os.path.basename(path)
            match = re.match(self.filename_format, filename)
            if match:
                try:
                    date = datetime.date(int(match.group("year")), int(match.group("month")), int(match.group("date")))
                    if date >= min_date:
                        result.append((path, date))
                except ValueError:
                    self.log.failure("Couldn't parse date from {filename}", filename=filename)
            else:
                self.log.failure("Couldn't parse date from {filename} (does it match the configured pattern?)", filename=filename)
        return result

    def grep_lines(self, channel, min_date, search, uploader):
        callback = defer.Deferred()
        find_files = threads.deferToThread(self._find_files, min_date, channel)
        find_files.addCallback(lambda files: threads.deferToThread(self._grep_lines, search, files, callback, uploader))
        find_files.addErrback(lambda error: callback.errback(error))
        return callback

    def _grep_lines(self, search, files, callback, uploader):
        defers = []
        for logfile in files:
            defers.append(threads.deferToThread(self._grep_lines_in_file, logfile, search))
        dl = defer.DeferredList(defers, consumeErrors=True)
        dl.addCallback(lambda results: threads.deferToThread(self._generate_grep_report, results, callback, search, uploader).chainDeferred(callback))

    def _generate_grep_report(self, results, callback, pattern, uploader):
        lines_by_date = {}
        for success, result in results:
            if success:
                date, lines = result

                if lines:
                    lines_by_date[date] = lines
            else:
                self.log.failure("Couldn't grep lines", failure=result)
        report = ""
        type_count = {}
        total_lines = 0
        for date in sorted(lines_by_date):
            report += "----- %s -----\n" % date
            for entry in lines_by_date[date]:
                type, line, parts = entry
                report += "%s\n" % line
                if not type in type_count:
                    type_count[type] = 0
                type_count[type] += 1
                total_lines += 1
            report += "\n\n"

        header = ""
        for type, count in type_count.iteritems():
            header += "%s: %d%% (%d lines)\n" % (type, float(count) / total_lines * 100, count)

        if total_lines:
            url = uploader._upload_text("Showing %d log lines over %d days for search pattern: %s\n%s\n%s" % (total_lines, len(lines_by_date), pattern.pattern, header, report))
            return "%d log lines found - %s" % (total_lines, url)
        else:
            return "Sorry, but I couldn't find anything :("

    def stalk_user(self, channel, min_date, search, uploader):
        callback = defer.Deferred()
        find_files = threads.deferToThread(self._find_files, min_date, channel)
        find_files.addCallback(lambda files: threads.deferToThread(self._stalk_user, search, files, callback, uploader))
        find_files.addErrback(lambda error: callback.errback(error))
        return callback

    def _stalk_user(self, search, files, callback, uploader):
        defers = []
        for logfile in files:
            defers.append(threads.deferToThread(self._find_victims_in_file, logfile))
        dl = defer.DeferredList(defers, consumeErrors=True)
        dl.addCallback(lambda results: threads.deferToThread(self._generate_stalk_report, results, callback, search, uploader).chainDeferred(callback))

    def _generate_stalk_report(self, results, callback, search, uploader):
        by_nick = {}
        all_victims = set()
        for success, result in results:
            if success:
                date, victims = result

                if victims:
                    for nick, victim in victims.iteritems():
                        if nick in by_nick:
                            record = by_nick[nick]
                            record.merge(victim)
                        else:
                            by_nick[nick] = victim
                            all_victims.add(victim)
            else:
                self.log.failure("Couldn't grep lines", failure=result)

        if search not in by_nick:
            return "Sorry, but I couldn't find that user :( (Make sure you use an exact username, case sensitive)"
        victim = by_nick[search]

        self.log.info("Merging down users for stalk ({victims} victims)", victims=len(all_victims))
        for iteration in range(3):
            to_merge = {}
            for other_victim in all_victims:
                if other_victim is not victim:
                    similarity = victim.is_similar(other_victim)
                    if similarity:
                        to_merge[other_victim] = similarity
            for merge, reason in to_merge.iteritems():
                all_victims.remove(merge)
                victim.merge(merge, reason)
        self.log.info("Merged down into {victims} victims", victims=len(all_victims))

        if len(victim.nicks) == 1 and len(victim.idents) == 1 and len(victim.hosts) == 1:
            return "I have only ever seen that user under 1 nick/ident/host."
        report = "Showing %d nick(s) / %d ident(s) / %d host(s) for %s\n\n" % (len(victim.nicks), len(victim.idents), len(victim.hosts), search)
        report += "Nicks:\n"
        for nick in victim.nicks:
            report += "\t%s(%s)\n" % (nick.ljust(75), ", ".join(victim.nicks[nick]))
        report += "\nIdents:\n"
        for ident in victim.idents:
            report += "\t%s(%s)\n" % (ident.ljust(75), ", ".join(victim.idents[ident]))
        report += "\nHosts:\n"
        for host in victim.hosts:
            report += "\t%s(%s)\n" % (host.ljust(75), ", ".join(victim.hosts[host]))
        report += "\nFull masks:\n"
        report += "\t%s%s%s\n" % ("".ljust(100), "First Seen".ljust(25), "Last Seen".ljust(25))
        mask_report = ""
        earliest = None
        latest = None
        for mask in sorted(victim.full_masks, key=lambda mask: victim.full_masks[mask]['first_seen']):
            entry = victim.full_masks[mask]
            mask_report += "\t%s%s%s\n" % (mask.ljust(100), str(entry['first_seen']).ljust(25), str(entry['last_seen']).ljust(25))
            if earliest is None or entry['first_seen'] < earliest:
                earliest = entry['first_seen']
            if latest is None or entry['last_seen'] > latest:
                latest = entry['last_seen']
        report += "\t%s%s\n" % ("".ljust(100), str(earliest).ljust(25))
        report += mask_report
        report += "\t%s%s%s\n" % ("".ljust(100), "".ljust(25), str(latest).ljust(25))

        url = uploader._upload_text(report)
        return "%d nick(s) / %d ident(s) / %d host(s) for %s - %s" % (len(victim.nicks), len(victim.idents), len(victim.hosts), search, url)


class GreppyPieBot(irc.IRCClient):
    log = Logger()

    def __init__(self, factory):
        self.factory = factory
        self.uploader = TextUploader()
        self.nickname = factory.config['nickname']
        self.realname = factory.config['realname']
        self.password = factory.config['server']['password']
        self.history = MessageHistory(factory.config['logs'], factory.config['log-filename'], factory.config['format'])

    def signedOn(self):
        for channel in self.factory.config['join']:
            self.join(channel)

    def privmsg(self, user, channel, msg):
        nick = user.split('!', 1)[0]
        msg = msg.strip()

        if user == 'NickServ!NickServ@services.' and 'You are now identified' in msg:
            for channel in self.factory.config['join']:
                connection.join(channel)
            return

        if channel != self.nickname:
            args = re.split(r"\b\S?\s", msg, 1, flags=re.UNICODE)
            if len(args) > 1 and args[0].lower() == self.nickname.lower():
                msg = args[1].strip()

                match = re.match(r"^stalk (?P<channel>[#\w]+) (?P<date>\S+) (?P<search>.+)$", msg, flags=re.UNICODE)
                if match:
                    date = self._parse_date(match.group("date"))
                    target = match.group("channel").lower()
                    if not target in self.factory.config['channels']:
                        self.msg(channel, "%s: I'm sorry, but I can't let you look at my %s logs." % (nick, target))
                        return
                    if date:
                        req = self.history.stalk_user(target, date, match.group("search"), self.uploader)
                        req.addCallback(lambda output: self.msg(channel, "%s: %s" % (nick, output)))
                        req.addErrback(lambda error: self._report_error("%s: Sorry, but I got an error (%s) searching for that :(" % (nick, error.__class__.__name__), channel, error))
                    else:
                        self.msg(channel, "%s: I'm sorry, but that's an invalid date. (Valid examples: '-', 'today', '2015', '2011-02', 2011-02-03', etc)" % nick)
                    return

                match = re.match(r"^(?P<channel>[#\w]+) (?P<date>\S+) (?P<search>.+)$", msg, flags=re.UNICODE)
                if match:
                    date = self._parse_date(match.group("date"))
                    target = match.group("channel").lower()
                    try:
                        pattern = re.compile(match.group("search"), re.IGNORECASE)
                    except re.error as e:
                        self.msg(channel, "%s: I'm sorry, but that is an invalid pattern (%s)" % (nick, e))
                        return
                    if not target in self.factory.config['channels']:
                        self.msg(channel, "%s: I'm sorry, but I can't let you look at my %s logs." % (nick, target))
                        return
                    if date:
                        req = self.history.grep_lines(target, date, pattern, self.uploader)
                        req.addCallback(lambda output: self.msg(channel, "%s: %s" % (nick, output)))
                        req.addErrback(lambda error: self._report_error("%s: Sorry, but I got an error (%s) searching for that :(" % (nick, error.__class__.__name__), channel, error))
                    else:
                        self.msg(channel, "%s: I'm sorry, but that's an invalid date. (Valid examples: '-', 'today', '2015', '2011-02', 2011-02-03', etc)" % nick)
                    return
                
                self.msg(channel, "%s: I'm sorry, I don't know what you mean :(" % nick)

    def action(self, user, channel, data):
        nick = user.split('!', 1)[0]
        split = data.rstrip().split(" ")

        if channel == self.nickname:
            channel = nick

        if len(split) == 2 and split[1] == self.nickname:
            self.describe(channel, "%s %s <3" % (split[0], nick))

    def _report_error(self, message, channel, error):
        self.log.failure(message, failure=error)
        self.msg(channel, message)

    def _parse_date(self, input):
        if input == "-" or input == "*":
            return datetime.date.min

        if input == "today":
            return datetime.date.today()

        match = re.match(r"^(?P<year>\d{1,4})(?:|(?P<separator>[-\./\\]?)(?:(?P<month>\d{1,2})(?:|(?P=separator)(?P<day>\d{1,2}))))$", input, flags=re.UNICODE)
        if match:
            now = datetime.date.today()
            year = int(match.group("year").rjust(4, "0"))
            if match.group("month"):
                month = int(match.group("month").rjust(2, "0"))
            else:
                month = 1
            if match.group("day"):
                day = int(match.group("day").rjust(2, "0")) or 1
            else:
                day = 1
            try:
                return datetime.date(year, month, day)
            except ValueError:
                return None

        return None


class GreppyPieFactory(protocol.ClientFactory):
    def __init__(self, config_file):
        self.config_file = config_file
        self.load_config()

    def buildProtocol(self, addr):
        p = GreppyPieBot(self)
        return p

    def clientConnectionLost(self, connector, reason):
        connector.connect()

    def clientConnectionFailed(self, connector, reason):
        print "connection failed:", reason
        reactor.stop()

    def load_config(self):
        with open(self.config_file, 'r') as file:
            self.config = yaml.load(file)

    def save_config(self):
        with open(self.config_file, 'w') as file:
            yaml.dump(self.config, file, default_flow_style=False)


if __name__ == '__main__':
    if len(sys.argv) > 2 or len(sys.argv) > 1 and sys.argv[1] == '--help':
        print("Usage: greppypie.py [config.yml]")
        sys.exit(1)
    config = 'config.yml'
    if len(sys.argv) > 1:
        config = sys.argv[1]
    globalLogPublisher.addObserver(textFileLogObserver(sys.stdout))
    factory = GreppyPieFactory(config)
    reactor.connectTCP(factory.config['server']['host'], factory.config['server']['port'], factory)

    # run bot
    reactor.run()