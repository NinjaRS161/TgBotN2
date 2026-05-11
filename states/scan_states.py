from aiogram.fsm.state import State, StatesGroup


class ScanStates(StatesGroup):
    waiting_channel_link = State()
    waiting_usernames_for_channel_check = State()
