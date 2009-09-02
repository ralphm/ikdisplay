// import Nevow.Athena
// import Divmod.Runtime
// import Divmod.Defer
// import jQuery
// import jQueryUI
// import backchannel

backChannel.options.itemAmount = 13;
Notifier.Notifier = Nevow.Athena.Widget.subclass("Notifier.Notifier");
Notifier.Notifier.methods(
    function renderNotification(self, notification) {
        notification.text = notification.subtitle
        d = Divmod.Defer.Deferred()
        backChannel.addMessage(notification, function() {d.callback(null);});
        return d;
    }
);
