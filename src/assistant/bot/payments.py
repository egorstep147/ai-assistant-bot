"""Оплата Pro.
- Telegram Stars: ежемесячная подписка, которую Telegram продлевает сам (subscription_period).
  Stars разрешены для цифровых услуг — ассистент как раз такая.
- Карта через провайдера из @BotFather (ЮKassa): разовая оплата pro_days дней.
Каждое списание (в том числе автопродление) приходит как successful_payment."""

import logging
from datetime import UTC, datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from assistant.config import settings
from assistant.db import Session
from assistant.db.models import Payment, User
from assistant.services import billing, users

log = logging.getLogger(__name__)
router = Router()
MONTH = 30 * 24 * 3600  # Telegram поддерживает только такой период подписки


def _prices() -> dict[str, tuple[str, int]]:
    """payload → (валюта, сумма в минимальных единицах). Сверяем с ним каждый платёж."""
    p = {"pro:stars": ("XTR", settings.pro_price_stars)}
    if settings.payment_token.get_secret_value():
        p["pro:card"] = ("RUB", settings.pro_price_rub * 100)
    return p


async def _plan_text(user: User) -> str:
    async with Session() as s:
        used = await billing.usage_today(s, user.id)
    p = billing.plan(user)
    until = f" до {user.pro_until:%d.%m.%Y}" if p.name == "Pro" else ""
    return (
        f"💎 Ваш тариф: {p.name}{until}\nСегодня: {used.messages}/{p.messages} сообщений, "
        f"{used.files}/{p.files} файлов\n\n"
        f"Pro: {settings.pro_messages} сообщений и {settings.pro_files} файлов в день, более умная модель.\n"
        f"Цена: {settings.pro_price_stars} ⭐ в месяц"
        + (f" или {settings.pro_price_rub} ₽ картой за {settings.pro_days} дней" if "pro:card" in _prices() else "")
    )


async def _plan_kb(bot: Bot):
    kb = InlineKeyboardBuilder()
    stars = await bot.create_invoice_link(
        title="Pro на месяц",
        description="Больше сообщений и файлов, умнее модель. Продлевается автоматически.",
        payload="pro:stars",
        currency="XTR",
        prices=[LabeledPrice(label="Pro", amount=settings.pro_price_stars)],
        subscription_period=MONTH,
    )
    kb.button(text=f"⭐ {settings.pro_price_stars} Stars / месяц", url=stars)
    if "pro:card" in _prices():
        kb.button(text=f"💳 {settings.pro_price_rub} ₽ картой", callback_data="pay:card")
    kb.adjust(1)
    return kb.as_markup()


@router.message(Command("plan"))
async def plan_cmd(message: Message, bot: Bot) -> None:
    async with Session() as s, s.begin():
        user = await users.touch(s, message.from_user)
    await message.answer(await _plan_text(user), reply_markup=await _plan_kb(bot))


@router.callback_query(F.data == "plan")
async def plan_button(callback: CallbackQuery, bot: Bot) -> None:
    async with Session() as s, s.begin():
        user = await users.touch(s, callback.from_user)
    await callback.message.answer(await _plan_text(user), reply_markup=await _plan_kb(bot))
    await callback.answer()


@router.callback_query(F.data == "pay:card")
async def pay_card(callback: CallbackQuery, bot: Bot) -> None:
    await bot.send_invoice(
        callback.from_user.id,
        title=f"Pro на {settings.pro_days} дней",
        description="Больше сообщений и файлов, умнее модель.",
        payload="pro:card",
        provider_token=settings.payment_token.get_secret_value(),
        currency="RUB",
        prices=[LabeledPrice(label="Pro", amount=settings.pro_price_rub * 100)],
    )
    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    # Сумма и валюта должны совпасть с текущим прайсом: старый счёт по старой цене не пройдёт
    if _prices().get(query.invoice_payload) == (query.currency, query.total_amount):
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Цена изменилась — откройте /plan заново")


@router.message(F.successful_payment)
async def paid(message: Message) -> None:
    p = message.successful_payment
    async with Session() as s:
        try:
            async with s.begin():
                user = await users.touch(s, message.from_user)
                if p.subscription_expiration_date:  # подписка Stars: Telegram сам сообщает, до какого числа
                    until = await billing.extend_pro(
                        s, user, until=datetime.fromtimestamp(p.subscription_expiration_date, UTC)
                    )
                else:
                    until = await billing.extend_pro(s, user, days=settings.pro_days)
                s.add(
                    Payment(
                        user_id=user.id,
                        provider="stars" if p.currency == "XTR" else "card",
                        amount=p.total_amount if p.currency == "XTR" else p.total_amount // 100,
                        currency=p.currency,
                        charge_id=p.telegram_payment_charge_id,
                        provider_charge_id=p.provider_payment_charge_id or "",
                        pro_until=until,
                    )
                )
        except IntegrityError:
            log.warning("Повтор платежа %s — уже учтён", p.telegram_payment_charge_id)
            return
    log.info("Оплата от %s: %s %s, Pro до %s", user.id, p.total_amount, p.currency, until)
    await message.answer(f"💎 Спасибо! Pro активен до {until:%d.%m.%Y}.")


async def refund(bot: Bot, charge_id: str) -> str:
    """Возврат Stars (для админа). Картой возвращают в личном кабинете ЮKassa."""
    async with Session() as s, s.begin():
        pay = await s.scalar(select(Payment).where(Payment.charge_id == charge_id))
        if pay is None or pay.refunded:
            return "Платёж не найден или уже возвращён"
        if pay.provider != "stars":
            return "Это оплата картой — верните её в кабинете ЮKassa"
        await bot.refund_star_payment(pay.user_id, charge_id)
        pay.refunded = True
        user = await s.get(User, pay.user_id)
        user.pro_until = datetime.now(UTC)
    return f"Возвращено {pay.amount} ⭐ пользователю {pay.user_id}, Pro снят"
