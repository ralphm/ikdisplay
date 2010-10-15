from twisted.application import service
from twisted.internet import defer, error, reactor
from twisted.python import log
from twisted.web import error as http_error
from twisted.web.client import HTTPDownloader, _parse
from twisted.words.xish import domish

from wokkel import pubsub

from twittytwister import twitter, txml

from ikdisplay.source import TwitterSource

NS_TWITTER = 'http://mediamatic.nl/ns/ikdisplay/2009/twitter'

def downloadPageWithFactory(url, file, contextFactory=None, *args, **kwargs):
    """
    Download a web page to a file and return the request factory.

    The returned factory has a C{loseConnnection} method to drop the
    connection. This is especially useful for stopping streamed responses.

    @param file: path to file on filesystem, or file-like object.
    @see L{HTTPDownloader} to see what extra args can be passed.
    """
    class HTTPDownloaderSavingProtocol(HTTPDownloader):
        lastProtocol = None

        def buildProtocol(self, addr):
            p = HTTPDownloader.buildProtocol(self, addr)
            self._lastProtocol = p
            return p

        def loseConnection(self):
            if self._lastProtocol:
                self._lastProtocol.transport.loseConnection()

    scheme, host, port, path = _parse(url)
    factory = HTTPDownloaderSavingProtocol(url, file, *args, **kwargs)
    reactor.connectTCP(host, port, factory)
    return factory


class TwitterFeedWithFactory(twitter.TwitterFeed):
    """
    Twitter Feed returning factories.

    Where L{TwitterFeed}'s methods return the deferred that fires for requests,
    this class returns the whole request factory. This enables manual
    disconnects.
    """

    def _rtfeed(self, url, delegate, args):
        if args:
            url += '?' + self._urlencode(args)
        log.msg('Fetching %s' % url)
        return downloadPageWithFactory(url,
                                       txml.HoseFeed(delegate),
                                       agent=self.agent,
                                       headers=self._makeAuthHeader("GET", url, args))


class TwitterMonitor(service.Service):
    """
    Reconnecting Twitter monitor service.

    @ivar terms: Terms to track as an iterable of C{unicode}.
    @ivar userIDs: IDs of users to follow as an iterable of C{unicode}.
    """

    initialDelay = 5
    delay = 5
    maxDelay = 5
    continueTrying = True
    errorState = None
    consumer = None
    factory = None

    terms = None
    userIDs = None

    def __init__(self, username, password, consumer=None):
        self.controller = TwitterFeedWithFactory(username, password)
        self.consumer = consumer


    def startService(self):
        self.continueTrying = True
        self.doConnect()


    def stopService(self):
        self.continueTrying = False

        if self.factory:
            self.factory.loseConnection()


    def doConnect(self):

        def forgetFactory(result):
            self.factory = None
            return result

        def cb(_):
            log.msg("Connection closed cleanly.")
            self.errorState = None
            self.delay = self.initialDelay

        def trapConnectError(failure):
            failure.trap(error.ConnectError,
                         error.TimeoutError,
                         error.ConnectionClosed)
            log.err(failure)
            if self.errorState != 'connect':
                self.errorState = 'connect'
                self.delay = 0.25
                self.maxDelay = 16
            else:
                self.delay = min(self.maxDelay, self.delay * 2)


        def trapHTTPError(failure):
            failure.trap(http_error.Error)
            log.err(failure, "HTTP error")

            if self.errorState != 'http':
                self.errorState = 'http'
                self.delay = 10
                self.maxDelay = 240
            else:
                self.delay = min(self.maxDelay, self.delay * 2)

        def trapOtherErrors(failure):
            log.err(failure)
            self.errorState = 'other'
            self.continueTrying = False

        def retry(_):
            if self.continueTrying:
                if self.delay == 0:
                    when = "now"
                else:
                    when = "in %0.2f seconds" % (self.delay,)
                log.msg("Reconnecting %s." % (when,))
                reactor.callLater(self.delay, self.doConnect)
            else:
                log.msg("Abandoning reconnect.")

        if not self.terms and not self.userIDs:
            log.msg("No Twitter terms or users to filter on. Not connecting.")
            return False

        args = {}
        if self.terms:
            args['track'] = ','.join(self.terms)
        if self.userIDs:
            args['follow'] = ','.join(self.userIDs)

        if self.controller is None:
            log.msg("No Twitter consumer set. Not connecting.")
            return False

        self.factory = self.controller.filter(self.consumer.onEntry, args)
        d = self.factory.deferred
        d.addBoth(forgetFactory)
        d.addCallback(cb)
        d.addErrback(trapConnectError)
        d.addErrback(trapHTTPError)
        d.addErrback(trapOtherErrors)
        d.addCallback(retry)

        return True


    def setFilters(self, terms, userIDs):
        """
        Set the terms to track and users to follow and (re)connect.

        @param terms: Terms to track as an iterable of C{unicode}.
        @param userIDs: IDs of users to follow as an iterable of C{unicode}.
        """
        self.terms = terms
        self.userIDs = userIDs

        if self.factory:
            # If connected, lose connection to automatically reconnect.
            self.factory.loseConnection()
        elif self.running and self.controller:
            # Start connecting.
            self.doConnect()



class TwitterLogger(object):
    """
    Logging Twitter consumer.
    """

    def onEntry(self, entry):
        log.msg((u"%s: %s" % (entry.user.screen_name,
                              entry.text)).encode('utf-8'))



def propertyToDomish(prop):
    element = domish.Element((NS_TWITTER, prop.tag_name))

    for propName in prop.SIMPLE_PROPS:
        if hasattr(prop, propName):
            value = getattr(prop, propName)
            element.addElement(propName, content=value)

    for propName in prop.COMPLEX_PROPS:
        if hasattr(prop, propName):
            child = propertyToDomish(getattr(prop, propName))
            element.addChild(child)

    return element



class TwitterPubSubClient(pubsub.PubSubClient):

    def __init__(self, service, nodeIdentifier):
        self.service = service
        self.nodeIdentifier = nodeIdentifier
        self.queue = defer.DeferredQueue()
        self._initialized = False


    def connectionInitialized(self):
        pubsub.PubSubClient.connectionInitialized(self)
        self._initialized = True
        self.processQueue()


    def connectionLost(self, reason):
        self._initialized = False


    def processQueue(self):
        def publishItem(item):
            def publishFailed(failure):
                log.err(failure)
                log.msg("Requeueing")
                self.queue.put(item)

            d = self.publish(self.service, self.nodeIdentifier, [item])
            d.addErrback(publishFailed)
            return d

        if not self._initialized:
            return

        d = self.queue.get()
        d.addCallback(publishItem)
        d.addCallback(lambda _: reactor.callLater(0, self.processQueue))


    def onEntry(self, entry):
        payload = propertyToDomish(entry)
        item = pubsub.Item(entry.id, payload)
        self.queue.put(item)



class TwitterDispatcher(object):
    """
    Dispatches statuses to enabled observers.

    Observers are enabled L{TwitterSource} items. The terms to track and
    userIDs to follow are collected from the observers and their unions are
    used to pass as the filter for Twitter's Streaming API. Incoming statuses
    are passed to all observers, who can then filter out the desired statuses
    themselves.

    Call C{refreshFilters} after adding, removing, or changing observers to
    recalculate the filter and reconnect.
    """

    def __init__(self, store, monitor):
        self.store = store
        self.monitor = monitor
        self.refreshFilters()


    def _getEnabledSources(self):
        return self.store.query(TwitterSource, TwitterSource.enabled==True)


    def collectFilters(self):
        terms = set()
        userIDs = set()

        for source in self._getEnabledSources():
            terms.update(source.terms)
            userIDs.update(source.userIDs)

        return terms, userIDs


    def refreshFilters(self):
        self.monitor.setFilters(*self.collectFilters())


    def onEntry(self, entry):
        for source in self._getEnabledSources():
            source.onEntry(entry)
