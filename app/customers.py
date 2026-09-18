"""Customer records and customer-specific price lists, as edited from the approval app."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .errors import Conflict, NotFound
from .models import Customer, CustomerPrice, Product, TaxRate
from .pricing import money, tier_unit_price
from .schemas import CustomerListItem, CustomerOut, CustomerPriceIn, CustomerPriceOut, CustomerUpdate

REQUIRED_FIELDS = frozenset({"name", "discount_pct", "payment_terms", "is_verified"})


def _get(session: Session, customer_id: int) -> Customer:
    customer = session.get(Customer, customer_id)
    if customer is None:
        raise NotFound(f"Customer {customer_id} not found")
    return customer


def _price_counts(session: Session) -> dict[int, int]:
    rows = session.execute(select(CustomerPrice.customer_id, func.count()).group_by(CustomerPrice.customer_id))
    return dict(rows.all())


def _item(customer: Customer, price_count: int) -> CustomerListItem:
    return CustomerListItem(
        **CustomerOut.model_validate(customer).model_dump(),
        billing_address=customer.billing_address,
        price_count=price_count,
    )


def _price_out(price: CustomerPrice) -> CustomerPriceOut:
    product = price.product
    return CustomerPriceOut(
        id=price.id,
        sku=product.sku,
        product_name=product.name,
        unit=product.unit,
        min_qty=price.min_qty,
        unit_price=price.unit_price,
        list_price=tier_unit_price(product, Decimal(price.min_qty)),
        valid_from=price.valid_from,
        valid_until=price.valid_until,
        note=price.note,
    )


def list_customers(session: Session, q: str = "") -> list[CustomerListItem]:
    query = select(Customer).order_by(Customer.company.is_(None), Customer.company, Customer.name).limit(500)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.where(or_(Customer.name.ilike(like), Customer.email.ilike(like), Customer.company.ilike(like)))
    counts = _price_counts(session)
    return [_item(customer, counts.get(customer.id, 0)) for customer in session.scalars(query)]


def update_customer(session: Session, customer_id: int, data: CustomerUpdate) -> CustomerListItem:
    customer = _get(session, customer_id)
    for field_name, value in data.model_dump(exclude_unset=True).items():
        if value is None and field_name in REQUIRED_FIELDS:
            continue
        if field_name == "tax_region":
            value = (value or "").strip() or None
            if value and session.get(TaxRate, value) is None:
                raise Conflict(f"Unknown tax region '{value}'. Add it to the tax_rates table first.")
        setattr(customer, field_name, value)
    session.commit()
    return _item(customer, _price_counts(session).get(customer.id, 0))


def list_prices(session: Session, customer_id: int) -> list[CustomerPriceOut]:
    _get(session, customer_id)
    query = (
        select(CustomerPrice)
        .join(Product, CustomerPrice.product_id == Product.id)
        .where(CustomerPrice.customer_id == customer_id)
        .order_by(Product.sku, CustomerPrice.min_qty)
    )
    return [_price_out(price) for price in session.scalars(query).unique()]


def upsert_price(session: Session, customer_id: int, data: CustomerPriceIn) -> CustomerPriceOut:
    _get(session, customer_id)
    product = session.scalar(select(Product).where(Product.sku == data.sku.strip()))
    if product is None:
        raise Conflict(f"Unknown SKU: {data.sku}")
    if data.valid_from and data.valid_until and data.valid_until < data.valid_from:
        raise Conflict("'Valid until' is before 'valid from'")
    price = session.scalar(
        select(CustomerPrice).where(
            CustomerPrice.customer_id == customer_id,
            CustomerPrice.product_id == product.id,
            CustomerPrice.min_qty == data.min_qty,
        )
    )
    if price is None:
        price = CustomerPrice(customer_id=customer_id, product=product, min_qty=data.min_qty)
        session.add(price)
    price.unit_price = money(data.unit_price)
    price.valid_from = data.valid_from
    price.valid_until = data.valid_until
    price.note = data.note
    session.commit()
    return _price_out(price)


def delete_price(session: Session, customer_id: int, price_id: int) -> None:
    price = session.get(CustomerPrice, price_id)
    if price is None or price.customer_id != customer_id:
        raise NotFound("Price not found")
    session.delete(price)
    session.commit()
