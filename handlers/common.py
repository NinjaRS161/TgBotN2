from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message


router = Router()


@router.message(Command("cancel"))
@router.message(F.text.in_(["Отмена", "❌ Отмена"]))
async def cancel_current_action(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if not current_state:
        await message.answer("Сейчас нет активного действия.")
        return

    await state.clear()
    await message.answer("Текущее действие отменено. Можно начать заново.")
