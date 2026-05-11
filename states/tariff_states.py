from aiogram.fsm.state import StatesGroup, State

class TariffStates(StatesGroup):
    waiting_for_payment = State()
