from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Mode = Literal['walking', 'slow', 'aid', 'wheelchair', 'assistance', 'unsure']


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, allow_inf_nan=False)


class Needs(Model):
    mode: Mode = 'walking'
    can_stairs: bool = True
    step_free: bool = False
    accessible_toilet: bool = False
    speed_mps: float | None = Field(None, ge=0.2, le=2.0)
    max_slope: float = Field(8, ge=0, le=15)
    min_width: float = Field(0.9, ge=0.6, le=2)
    rest_minutes: float = Field(0, ge=0, le=30)
    confirmed: bool = False

    @model_validator(mode='after')
    def defaults(self):
        if self.mode in ('aid', 'wheelchair', 'assistance'):
            self.step_free = True
            self.can_stairs = False
        return self

    @property
    def speed(self):
        return (
            self.speed_mps
            or {
                'walking': 1.2,
                'slow': 0.7,
                'aid': 0.6,
                'wheelchair': 0.8,
                'assistance': 0.5,
                'unsure': 0.7,
            }[self.mode]
        )


class Profile(Model):
    age: int | None = Field(None, ge=0, le=120)
    age_band: Literal['under18', '18to64', '65plus'] | None = None
    needs: Needs | None = None
    consent: bool = False

    @model_validator(mode='after')
    def required(self):
        if self.age is None and self.age_band is None and self.needs is None:
            raise ValueError('至少填寫年齡或行動需求其中一項')
        if not self.consent:
            raise ValueError('需同意儲存導航需求；也可使用免登入訪客模式')
        return self


class Register(Model):
    username: str = Field(min_length=3, max_length=40, pattern=r'^[a-zA-Z0-9_.-]+$')
    password: str = Field(min_length=10, max_length=128)
    profile: Profile


class Login(Model):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=128)


class Plan(Model):
    request_id: str = Field(min_length=8, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    origin: str = Field(max_length=40)
    group_size: int = Field(1, ge=1, le=30)
    accessible_count: int = Field(0, ge=0, le=30)
    helper_present: bool = False
    needs: Needs
    shaking_stopped: bool
    evacuation_needed: bool
    max_extra_minutes: float = Field(5, ge=0, le=30)
    replace_id: str | None = Field(None, max_length=80)

    @model_validator(mode='after')
    def group(self):
        if self.accessible_count > self.group_size:
            raise ValueError('無障礙人數不可超過同行總人數')
        return self


class Transition(Model):
    action: Literal['accept', 'cancel', 'arrive', 'leave']


class Report(Model):
    request_id: str = Field(min_length=8, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    edge_id: str | None = Field(None, max_length=40)
    shelter_id: str | None = Field(None, max_length=40)
    kind: Literal['blocked', 'debris', 'fire', 'closure', 'crowding']
    observed_at: float
    description: str = Field(min_length=3, max_length=500)

    @model_validator(mode='after')
    def target(self):
        if bool(self.edge_id) == bool(self.shelter_id):
            raise ValueError('請選擇一個受影響路段或場所')
        if self.kind == 'closure' and not self.shelter_id:
            raise ValueError('場所關閉需指定場所')
        return self


class Review(Model):
    status: Literal['pending', 'avoiding', 'confirmed', 'resolved']
    note: str = Field(min_length=3, max_length=300)


class ShelterUpdate(Model):
    status: Literal['open', 'closed', 'unknown']
    capacity: int | None = Field(None, ge=0, le=100000)
    accessible_capacity: int | None = Field(None, ge=0, le=100000)
    background: int = Field(0, ge=0, le=100000)
    background_accessible: int = Field(0, ge=0, le=100000)
    entrance_step_free: bool | None
    space_accessible: bool | None
    accessible_toilet: bool | None
    note: str = Field(min_length=3, max_length=300)

    @model_validator(mode='after')
    def capacity_check(self):
        if self.accessible_capacity is not None and (
            self.capacity is None or self.accessible_capacity > self.capacity
        ):
            raise ValueError('無障礙適用容量不可超過總容量，且總容量必須已知')
        if self.background_accessible > self.background:
            raise ValueError('無障礙背景人數不可超過背景總人數')
        return self


class Scenario(Model):
    seed: int = Field(42, ge=0, le=2147483647)
    repeats: int = Field(3, ge=1, le=10)
    population: int = Field(300, ge=10, le=2000)
    evacuation_rate: float = Field(0.3, ge=0.01, le=1)
    app_rate: float = Field(0.6, ge=0, le=1)
    acceptance_rate: float = Field(1, ge=0, le=1)
    blocked_rate: float = Field(0.08, ge=0, le=0.5)
    false_report_rate: float = Field(0.03, ge=0, le=0.5)
    accessibility_rate: float = Field(0.1, ge=0, le=1)
    daytime_multiplier: float = Field(1, ge=0.3, le=3)
    max_extra_minutes: float = Field(5, ge=0, le=30)


class Event(Model):
    event_id: str = Field(min_length=1, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    version: int = Field(ge=1)
    occurred_at: float
    published_at: float
    title: str = Field(min_length=2, max_length=120)
    magnitude: float = Field(ge=0, le=10)
    drill: Literal[True] = True
    source: str = Field(min_length=2, max_length=120)
