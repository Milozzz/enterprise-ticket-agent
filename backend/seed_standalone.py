import asyncio
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, Float, DateTime, JSON, ForeignKey, Enum as SqlEnum
import enum

# 1. 定义模型 (复制自 models.py 以确保独立运行)
class Base(DeclarativeBase):
    pass

class UserRole(str, enum.Enum):
    USER = "USER"
    AGENT = "AGENT"
    MANAGER = "MANAGER"

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    email: Mapped[str] = mapped_column(String(100), unique=True)
    role: Mapped[UserRole] = mapped_column(SqlEnum(UserRole), default=UserRole.USER)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    orders: Mapped[list["Order"]] = relationship(back_populates="user")

class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    amount: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20))
    items: Mapped[dict] = mapped_column(JSON)
    shipping_address: Mapped[str] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    user: Mapped["User"] = relationship(back_populates="orders")

# 2. 数据填充逻辑
async def seed_data():
    DB_URL = "sqlite+aiosqlite:///./ticket.db"
    print(f"Using database: {DB_URL}")
    
    engine = create_async_engine(DB_URL)
    AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with AsyncSessionLocal() as session:
        # 创建测试用户
        users = [
            User(name="客服小李", email="agent@example.com", role=UserRole.AGENT),
            User(name="主管王总", email="manager@example.com", role=UserRole.MANAGER),
            User(name="普通用户张三", email="user@example.com", role=UserRole.USER),
        ]
        
        for user in users:
            stmt = select(User).where(User.email == user.email)
            result = await session.execute(stmt)
            if not result.scalar_one_or_none():
                print(f"Adding user: {user.name}")
                session.add(user)
        
        await session.commit()
        
        # 获取用户 ID
        stmt = select(User).where(User.email == "user@example.com")
        result = await session.execute(stmt)
        user_zhang = result.scalar_one()
        
        # 创建测试订单
        orders = [
            Order(
                id="789012",
                user_id=user_zhang.id,
                amount=1299.0,
                status="delivered",
                items=[{
                    "id": "item_1",
                    "name": "智能降噪耳机 Pro",
                    "quantity": 1,
                    "price": 1299.0,
                    "imageUrl": "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=200&h=200&fit=crop"
                }],
                shipping_address="北京市朝阳区某某街道 101 号",
                created_at=datetime.utcnow()
            ),
            Order(
                id="123456",
                user_id=user_zhang.id,
                amount=299.0,
                status="delivered",
                items=[{
                    "id": "item_2",
                    "name": "无线充电宝 20000mAh",
                    "quantity": 1,
                    "price": 299.0,
                    "imageUrl": "https://images.unsplash.com/photo-1609091839311-d536801ff141?w=200&h=200&fit=crop"
                }],
                shipping_address="上海市浦东新区某某路 202 号",
                created_at=datetime.utcnow()
            )
        ]
        
        for order in orders:
            stmt = select(Order).where(Order.id == order.id)
            result = await session.execute(stmt)
            if not result.scalar_one_or_none():
                print(f"Adding order: {order.id}")
                session.add(order)
        
        await session.commit()
        print("Seed data inserted successfully!")
    
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(seed_data())
