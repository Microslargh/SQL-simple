from datetime import datetime
from typing import List, Optional
from sqlalchemy import Column, Text, BigInteger, DateTime, Boolean, Identity
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import SQLModel, Field


from enum import Enum

class CustomPromptTypeEnum(str, Enum):
    GENERATE_SQL = "GENERATE_SQL"
    ANALYSIS = "ANALYSIS"
    PREDICT_DATA = "PREDICT_DATA"


class CustomPrompt(SQLModel, table=True):
    __tablename__ = "custom_prompt"
    id: Optional[int] = Field(sa_column=Column(BigInteger, Identity(always=False), primary_key=True))
    oid: Optional[int] = Field(sa_column=Column(BigInteger, nullable=True))
    type: Optional[str] = Field(max_length=20, nullable=True)  # GENERATE_SQL, ANALYSIS, PREDICT_DATA
    create_time: Optional[datetime] = Field(sa_column=Column(DateTime(timezone=False), nullable=True))
    name: Optional[str] = Field(max_length=255, nullable=True)
    prompt: Optional[str] = Field(sa_column=Column(Text, nullable=True))
    specific_ds: Optional[bool] = Field(sa_column=Column(Boolean, nullable=True))
    datasource_ids: Optional[List[int]] = Field(sa_column=Column(JSONB), default=[])


class CustomPromptCreator(SQLModel):
    type: str
    name: str
    prompt: str
    specific_ds: bool = False
    datasource_ids: List[int] = []


class CustomPromptEditor(SQLModel):
    id: int
    type: Optional[str] = None
    name: Optional[str] = None
    prompt: Optional[str] = None
    specific_ds: Optional[bool] = None
    datasource_ids: Optional[List[int]] = None

