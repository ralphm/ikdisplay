# -*- test-case-name: ikdisplay.test.test_xmpp -*-

from twisted.internet import defer, reactor, task
from twisted.python import log
from twisted.words.protocols.jabber import error
from twisted.words.protocols.jabber.jid import internJID as JID
from twisted.words.protocols.jabber.xmlstream import IQ
from twisted.words.xish import domish

from wokkel.client import XMPPClient
from wokkel.generic import parseXml
from wokkel.ping import PingClientProtocol
from wokkel.pubsub import Item, PubSubClient
from wokkel.xmppim import MessageProtocol, PresenceProtocol

NS_NOTIFICATION = 'http://mediamatic.nl/ns/ikdisplay/2009/notification'
NS_X_DELAY='jabber:x:delay'
NS_DELAY='urn:xmpp:delay'
NS_ATOM = 'http://www.w3.org/2005/Atom'

TEXTS = {
        'nl': {
            'via': u'via %s',
            'alien': u'Een illegale alien',
            'voted': u'stemde op %s',
            'present': u'is bij de ingang gesignaleerd',
            'alien_present': u'is bij de ingang tegengehouden',
            'ikcam_picture_singular': u'ging op de foto',
            'ikcam_picture_plural': u'gingen op de foto',
            'ikcam_event': u' bij %s',
            'diggs': u'eet graag %s',
            'flickr_upload': u'plaatste een plaatje',
            'flickr_more': u' (en nog %d meer)',
            },
        'en': {
            'via': u'via %s',
            'alien': u'An illegal alien',
            'voted': u'voted for %s',
            'present': u'was seen at the entrance',
            'alien_present': u'has been detained at the entrance',
            'ikcam_picture_singular': u'was photographed',
            'ikcam_picture_plural': u'were photographed',
            'ikcam_event': u' at %s',
            'diggs': u'diggs %s',
            'flickr_upload': u'posted a picture',
            'flickr_more': u' (and %d more)',
            },
        }

class PubSubClientFromAggregator(PubSubClient):
    """
    Publish-subscribe client that renders to notifications for aggregation.
    """

    def __init__(self, aggregator, nodes, language):
        self.aggregator = aggregator
        self.nodes = nodes
        self.texts = TEXTS[language]

    def connectionInitialized(self):
        """
        Called when the XMPP connection has been established.

        Subscribe to all the nodes with the JID we connected with.
        """
        PubSubClient.connectionInitialized(self)

        clientJID = self.parent.jid
        for service, nodeIdentifier in self.nodes:
            self.subscribe(service, nodeIdentifier, clientJID)


    def itemsReceived(self, event):
        """
        Called when items have been received.

        When items are received, an attempt is made to render them into
        notifications, and passed to the aggregator. The instance variable
        L{nods} keeps some information for each subscribed-to node, as a
        dictionary. The formatters are determined by the C{'type'} key in that
        dictionary.

        E.g. if the formatter is named C{'vote'}, the method C{format_vote}
        will be called with two arguments: the item payload as a
        L{domish.Element} and the node information dictionary.

        If items are received from unknown nodes, the subscription is
        cancelled.
        """
        if event.recipient != self.parent.jid:
            # This was not for us.
            return

        try:
            nodeInfo = self.nodes[event.sender, event.nodeIdentifier]
        except KeyError:
            msg = "Got event from %r, node %r. Unsubscribing"
            log.msg(msg % (event.sender, event.nodeIdentifier))
            self.unsubscribe(event.sender, event.nodeIdentifier,
                             event.recipient)

        nodeType = nodeInfo['type']
        processor = getattr(self, 'process_' + nodeType, self.processItems)
        processor(event, nodeInfo)


    def processItems(self, event, nodeInfo):
        nodeType = nodeInfo['type']
        try:
            formatter = getattr(self, 'format_' + nodeType)
        except AttributeError:
            log.msg("No formatter has been defined for "
                    "%r at %r (%s). Dropping." %
                    (event.nodeIdentifier, event.sender, nodeType))

        for item in event.items:
            try:
                element = item.elements().next()
            except (StopIteration):
                continue

            notification = formatter(element, nodeInfo)
            if 'via' in nodeInfo:
                notification['meta'] = self.texts['via'] % nodeInfo['via']

            if notification:
                self.aggregator.processNotification(notification)
            else:
                log.msg("Formatter returned None. Dropping.")


    def publishNotification(self, service, nodeIdentifier, notification):
        payload = domish.Element((NS_NOTIFICATION, 'notification'))

        for key, value in notification.iteritems():
            payload.addElement(key, content=value)

        def eb(failure):
            log.err(failure)

        d = self.publish(service, nodeIdentifier, [Item(payload=payload)])
        d.addErrback(eb)


    def _voteToName(self, vote):
        title = unicode(vote.person.title)
        if title:
            prefix = vote.person.prefix and (unicode(vote.person.prefix) + " ") or ""
            return prefix + title
        else:
            return None


    def _voteToAnswer(self, vote):
        answerID = unicode(vote.vote.answer_id_ref)
        for element in vote.question.answers.elements():
            if ((element.uri, element.name) == ('', 'item') and
                unicode(element.answer_id) == answerID):
                    return unicode(element.title)

        return None


    def format_vote(self, vote, nodeInfo):
        title = self._voteToName(vote)
        answer = self._voteToAnswer(vote)

        if not title:
            title = self.texts('alien')

        subtitle = self.texts['voted'] % (answer)

        notification = {
                'title': title,
                'subtitle': subtitle,
                'icon': unicode(vote.person.image),
                }

        try:
            voteType = nodeInfo['voteType']
            method = getattr(self, 'format_vote_%s' % voteType)
        except (AttributeError, KeyError):
            pass
        else:
            notification.update(method(vote))

        return notification

    def format_vote_presence(self, vote):
        title = self._voteToName(vote)

        if title:
            subtitle = self.texts['present']
        else:
            title = self.texts['alien']
            subtitle = self.texts['alien_present']


        return {"title": title,
                "subtitle": subtitle,
                }


    def format_vote_diggs(self, vote):
        answer = self._voteToAnswer(vote)
        subtitle = self.texts['diggs'] % (answer)
        return {"subtitle": subtitle}


    def format_status(self, status, nodeInfo):
        text = unicode(status.status).strip()
        if not text or text == 'is':
            return None

        return {'title': unicode(status.person.title),
                'subtitle': text,
                'icon': unicode(status.person.image)}


    def format_atom(self, entry, nodeInfo):
        import feedparser
        data = feedparser.parse(entry)
        return {'title': data.entries[0].title}


    def format_twitter(self, status, nodeInfo):
        return {'title': u'@' + unicode(status.user.screen_name),
                'subtitle': unicode(status.text),
                'icon': unicode(status.user.profile_image_url),
                }


    def format_ikcam(self, entry, nodeInfo):
        """
        Format an ikcam notification.
        """

        participants = [unicode(element)
                        for element in entry.participants.elements()
                        if element.name == 'participant']

        if not participants:
            return
        elif len(participants) == 1:
            subtitle = self.texts['ikcam_picture_singular']
        else:
            subtitle = self.texts['ikcam_picture_plural']

        if entry.event:
            subtitle += self.texts['ikcam_event'] % unicode(entry.event.title)


        return {'title': u', '.join(participants),
                'subtitle': subtitle,
                'icon': u'http://docs.mediamatic.nl/images/ikcam-80x80.png',
                'picture': unicode(entry.picture.rsc_uri),
                }


    def process_flickr(self, event, nodeInfo):
        import feedparser

        elements = (item.entry for item in event.items
                               if item.entry and item.entry.uri == NS_ATOM)

        feedDocument = domish.Element((NS_ATOM, 'feed'))
        for element in elements:
            feedDocument.addChild(element)

        feed = feedparser.parse(feedDocument.toXml().encode('utf-8'))
        entries = feed.entries

        entriesByAuthor = {}
        for entry in entries:
            if not hasattr(entry, 'enclosures'):
                return

            author = getattr(entry, 'author', None) or self.texts['alien']
            entriesByAuthor.setdefault(author, {'entry': entry, 'count': 0})
            entriesByAuthor[author]['count'] += 1

        for author, value in entriesByAuthor.iteritems():
            entry = value['entry']
            count = value['count']

            subtitle = self.texts['flickr_upload']
            if count > 1:
                subtitle += self.texts['flickr_more'] % (count - 1,)

            content = entry.content[0].value.encode('utf-8')
            parsedContent = parseXml(content)
            print parsedContent.toXml()

            uri = None

            for element in parsedContent.elements():
                if element.a and element.a.img:
                    uri = element.a.img['src']

            if uri:
                ext = uri.rsplit('.', 1)[1]
                uriParts = uri.split('_')
                uri = u'%s.%s' % (u'_'.join(uriParts[:-1]), ext)

            notification = {'title': author,
                            'subtitle': subtitle,
                            'meta': u"via %s" % nodeInfo['via'],
                            'picture': uri,
                            }
            self.aggregator.processNotification(notification)



class PresenceHandler(PresenceProtocol):

    def connectionInitialized(self):
        PresenceProtocol.connectionInitialized(self)
        self.available(priority=-1)



class GroupChatHandler(MessageProtocol):

    def __init__(self, aggregator, occupantJID):
        self.aggregator = aggregator
        self.occupantJID = occupantJID
        self.presenceHandler = None


    def connectionInitialized(self):
        MessageProtocol.connectionInitialized(self)

        if self.presenceHandler is None:
            # Look for a presence handler
            for handler in self.parent:
                if isinstance(handler, PresenceProtocol):
                    self.presenceHandler = handler
                    break

        # Send presence to the room to join
        if self.presenceHandler is not None:
            self.presenceHandler.available(recipient=self.occupantJID)
        else:
            log.msg("No presence handler available for this connection!")


    def onMessage(self, message):
        sender = JID(message['from'])

        if (sender.userhost() == self.occupantJID.userhost() and
            message['type'] == 'groupchat' and
            message.body and
            sender.resource and
            (not message.x or message.x.uri not in (NS_X_DELAY, NS_X_DELAY))):

            notification = {
                    u'title': sender.resource or u'*',
                    u'subtitle': unicode(message.body),
                    }
            self.aggregator.processNotification(notification)



class Pinger(PingClientProtocol):
    verbose = False

    def __init__(self, entity):
        self.entity = entity
        self.lc = task.LoopingCall(self.doPing)


    def connectionInitialized(self):
        self.lc.start(60)


    def connectionLost(self, reason):
        if self.lc.running:
            self.lc.stop()


    def doPing(self):
        if self.verbose:
            log.msg("*** PING ***")

        def cb(result):
            if self.verbose:
                log.msg("*** PONG ***")

        def eb(failure):
            failure.trap(error.StanzaError)
            exc = failure.value

            if exc.condition != 'remote-server-not-found':
                return failure

            log.msg("Remote server not found, restarting stream.")
            reactor.callLater(5, self.send, '</stream:stream>')

        d = self.ping(self.entity)
        d.addCallbacks(cb, eb)
        d.addErrback(log.err)
        return d



class PubSubClientFromNotifier(PubSubClient):
    """
    Publish-subscribe client that receives notifications for display.

    @ivar notifier: The notifier service.
    @ivar service: The publish-subscribe service
    @type service: L{JID}.
    @ivar nodeIdentifier: The publish-subscribe node.
    @type nodeIdentifier: L{unicode}.
    """

    maxHistory = 5

    def __init__(self, notifier, service, nodeIdentifier):
        PubSubClient.__init__(self)
        self.notifier = notifier
        self.service = service
        self.nodeIdentifier = nodeIdentifier

        self._subscribed = False
        self._gotHistory = False
        self._pendingHistory = set()
        self.history = []


    def connectionInitialized(self):
        """
        Called when the XMPP connection has been established.

        Subscribe to all the nodes with the JID we connected with.
        """
        PubSubClient.connectionInitialized(self)

        clientJID = self.parent.jid

        # Subscribe to the node we want to track
        if not self._subscribed:
            def cb(result):
                self._subscribed = True

            d = self.subscribe(self.service, self.nodeIdentifier, clientJID)
            d.addCallbacks(cb, log.err)

        # Retrieve history from the node
        if not self._gotHistory:
            def eb(failure):
                log.err(failure)
                return []

            def processHistory(notifications):
                self._gotHistory = True
                self.history = list(notifications)
                pending = self._pendingHistory
                self._pendingHistory = set()
                for d in pending:
                    reactor.callLater(0, d.callback, self.history)

            d = self.items(self.service, self.nodeIdentifier,
                                         maxItems=self.maxHistory)
            d.addErrback(eb)
            d.addCallback(reversed)
            d.addCallback(self._notificationsFromItems)
            d.addCallback(processHistory)


    def _notificationsFromItems(self, items):
        for item in items:
            try:
                payload = item.elements().next()
            except:
                continue

            if (payload.uri, payload.name) != (NS_NOTIFICATION,
                                               'notification'):
                continue

            notification = {}
            for element in payload.elements():
                notification[element.name] = unicode(element)

            yield notification


    def itemsReceived(self, event):
        """
        Called when items have been received.

        Items are notifications for display. Items received for other JIDs
        (including different resources of the JID we connect with) are dropped.
        If items are received from unknown nodes, the subscription is
        cancelled.

        @param event: The publish-subscribe event containing the items.
        @type event: L{pubsub.ItemsEvent}.
        """
        if event.recipient != self.parent.jid:
            # This was not for us.
            return
        elif (event.sender != self.service or
              event.nodeIdentifier != self.nodeIdentifier):
            log.msg("Got event from %r, node %r. Unsubscribing." % (
                event.sender, event.nodeIdentifier))
            self.unsubscribe(event.sender, event.nodeIdentifier,
                             event.recipient)
        else:
            for notification in self._notificationsFromItems(event.items):
                self.notifier.notify(notification)
                self.history.append(notification)
            self.history = self.history[-self.maxHistory:]


    def getHistory(self):
        if self._gotHistory:
            return defer.succeed(self.history)
        else:
            d = defer.Deferred()
            self._pendingHistory.add(d)
            return d



def makeService(config):
    if IQ.timeout is None:
        IQ.timeout = 30

    xmppService = XMPPClient(config['jid'], config['secret'])
    if config['verbose']:
        xmppService.logTraffic = True

    presenceHandler = PresenceHandler()
    presenceHandler.setHandlerParent(xmppService)

    pinger = Pinger(config['service'])
    pinger.setHandlerParent(xmppService)
    pinger.verbose = config['verbose']

    return xmppService
