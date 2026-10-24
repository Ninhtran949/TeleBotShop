import sqlalchemy as sa

from telebotshop.infrastructure.database.models.wallet import metadata

product_credit_prices = sa.Table(
    "product_credit_prices",
    metadata,
    sa.Column(
        "product_id",
        sa.Uuid,
        sa.ForeignKey("products.id", name="fk_credit_price_product"),
        primary_key=True,
    ),
    sa.Column("price_credits", sa.BigInteger, nullable=False),
    sa.CheckConstraint("price_credits BETWEEN 1 AND 999999999999999", name="ck_credit_price"),
)
