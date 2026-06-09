"""Random identity generation: Chinese name + age in a configured range."""
from __future__ import annotations

import random
from dataclasses import dataclass

# A compact set of common Chinese surnames and given-name characters. This is
# intentionally small and readable -- enough variety for test registrations on
# your own projects.
_SURNAMES = list(
    "王李张刘陈杨黄赵周吴徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢"
)
_GIVEN_CHARS = list(
    "伟芳娜秀英敏静丽强磊军洋勇艳杰娟涛明超秀霞平刚桂兰梅鑫晨宇浩然子轩思博文昊雨欣怡梓涵安宁乐瑞泽辰梦琪嘉宸"
)


@dataclass
class Identity:
    name: str
    age: int


def generate_chinese_name() -> str:
    """Return a random Chinese name: 1 surname + 1-2 given-name characters."""
    surname = random.choice(_SURNAMES)
    given_len = random.choice([1, 2])
    given = "".join(random.choice(_GIVEN_CHARS) for _ in range(given_len))
    return f"{surname}{given}"


def generate_age(min_age: int = 18, max_age: int = 45) -> int:
    lo, hi = sorted((int(min_age), int(max_age)))
    return random.randint(lo, hi)


def generate_identity(min_age: int = 18, max_age: int = 45) -> Identity:
    return Identity(name=generate_chinese_name(), age=generate_age(min_age, max_age))
