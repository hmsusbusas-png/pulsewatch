import unicodedata

from pydantic import BaseModel, ConfigDict, model_validator


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def plain_fields(self) -> "RequestModel":
        for name in ("name", "title", "slug", "group", "email"):
            value = getattr(self, name, None)
            if isinstance(value, str) and any(unicodedata.category(char) == "Cc" for char in value):
                raise ValueError("Управляющие символы запрещены")
        return self
