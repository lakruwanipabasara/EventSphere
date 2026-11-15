from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app import clock


class Base(DeclarativeBase):
    pass


def _now():
    return clock.now()


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default="attendee")  # admin | staff | attendee
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Venue(Base):
    __tablename__ = "venues"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    location: Mapped[str] = mapped_column(String(200), default="")
    capacity: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str] = mapped_column(String(500), default="")
    hourly_rate: Mapped[float] = mapped_column(Float, default=0)
    open_for_hire: Mapped[bool] = mapped_column(Boolean, default=False)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(50), default="Other")
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"))
    start: Mapped[datetime] = mapped_column(DateTime, index=True)
    end: Mapped[datetime] = mapped_column(DateTime)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | published | cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    venue: Mapped["Venue"] = relationship()
    categories: Mapped[list["TicketCategory"]] = relationship(
        back_populates="event", order_by="TicketCategory.id", cascade="all, delete-orphan")


class TicketCategory(Base):
    __tablename__ = "ticket_categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    price: Mapped[float] = mapped_column(Float, default=0)
    quantity: Mapped[int] = mapped_column(Integer)
    sold: Mapped[int] = mapped_column(Integer, default=0)
    held: Mapped[int] = mapped_column(Integer, default=0)  # seats held for waitlist offers
    event: Mapped["Event"] = relationship(back_populates="categories")

    @property
    def available(self):
        return max(self.quantity - self.sold - self.held, 0)


class Booking(Base):
    __tablename__ = "bookings"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("ticket_categories.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[float] = mapped_column(Float)
    total: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="confirmed")  # confirmed | cancelled
    payment_ref: Mapped[str] = mapped_column(String(40))
    refund_amount: Mapped[float] = mapped_column(Float, default=0)
    refund_ref: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    user: Mapped["User"] = relationship()
    event: Mapped["Event"] = relationship()
    category: Mapped["TicketCategory"] = relationship()
    tickets: Mapped[list["Ticket"]] = relationship(back_populates="booking", order_by="Ticket.id")


class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="valid")  # valid | used | cancelled
    scan_count: Mapped[int] = mapped_column(Integer, default=0)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    checked_in_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    booking: Mapped["Booking"] = relationship(back_populates="tickets")


class HireRequest(Base):
    __tablename__ = "hire_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"), index=True)
    start: Mapped[datetime] = mapped_column(DateTime)
    end: Mapped[datetime] = mapped_column(DateTime)
    purpose: Mapped[str] = mapped_column(String(300), default="")
    guests: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # confirmed | pending | rejected
    reason: Mapped[str] = mapped_column(String(400), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    user: Mapped["User"] = relationship()
    venue: Mapped["Venue"] = relationship()


class WaitlistEntry(Base):
    __tablename__ = "waitlist_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("ticket_categories.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="waiting")  # waiting|held|claimed|expired|cancelled
    hold_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    user: Mapped["User"] = relationship()
    event: Mapped["Event"] = relationship()
    category: Mapped["TicketCategory"] = relationship()


class AgentState(Base):
    """Persistent beliefs of each agent (survive restarts)."""
    __tablename__ = "agent_state"
    agent: Mapped[str] = mapped_column(String(50), primary_key=True)
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="0")


class MessageLog(Base):
    __tablename__ = "message_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(40), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_now)
    sender: Mapped[str] = mapped_column(String(60))
    receiver: Mapped[str] = mapped_column(String(60))
    performative: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    role: Mapped[str | None] = mapped_column(String(20), nullable=True)  # e.g. "admin" = all admins
    message: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
