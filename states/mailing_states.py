from aiogram.fsm.state import StatesGroup, State

class MailingStates(StatesGroup):
    count = State()
    text = State()
    delay = State()
    usernames = State()
    confirm = State()
