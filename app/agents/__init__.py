from app.bus import MessageBus

from app.agents.booking import BookingAgent
from app.agents.notification import NotificationAgent
from app.agents.ticket import TicketAgent
from app.agents.venue import VenueAgent
from app.agents.waitlist import WaitlistAgent


def build_bus() -> MessageBus:
    bus = MessageBus()
    for agent in (VenueAgent(), BookingAgent(), TicketAgent(), WaitlistAgent(), NotificationAgent()):
        bus.register(agent)
    return bus


bus = build_bus()
