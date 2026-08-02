"""Abstract base classes and canonical data types for navigator adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Position:
    lat_deg: Optional[float] = None
    lon_deg: Optional[float] = None
    alt_ft_msl: Optional[float] = None


@dataclass
class Waypoint:
    ident: Optional[str] = None
    lat_deg: Optional[float] = None
    lon_deg: Optional[float] = None


@dataclass
class NormalizedNavMessage:
    """Canonical navigation state produced by a ParserAdapter.

    All fields are optional; set only the fields the source sentence provides.
    The framework writes only non-None fields to the FIX database.
    """
    timestamp_utc: Optional[float] = None
    position: Position = field(default_factory=Position)
    ground_track_deg_true: Optional[float] = None
    ground_speed_kt: Optional[float] = None
    selected_course_deg: Optional[float] = None
    bearing_to_wp_deg: Optional[float] = None
    distance_to_wp_nm: Optional[float] = None
    cross_track_error_nm_signed: Optional[float] = None
    glideslope_error_dots: Optional[float] = None
    waypoint: Waypoint = field(default_factory=Waypoint)
    nav_mode: Optional[str] = None          # 'GPS', 'VLOC', 'APR', ...
    integrity: str = "valid"               # 'valid', 'degraded', 'invalid'
    gps_fix_type: Optional[int] = None     # 0=none, 1=GPS, 2=DGPS/WAAS


@dataclass
class AdapterHealth:
    parse_errors: int = 0
    stale: bool = False
    failed: bool = False
    last_sentence_type: Optional[str] = None


class ParserAdapter(ABC):
    """Interface every vendor parser must implement.

    parse_line() is the only required method. All others have safe defaults.
    """

    @abstractmethod
    def name(self) -> str:
        """Short identifier, e.g. 'garmin_nmea'. Must be unique in the registry."""

    @abstractmethod
    def supported_protocols(self) -> List[str]:
        """Human-readable list of supported sentence/protocol types."""

    @abstractmethod
    def parse_line(self, raw: bytes) -> List[NormalizedNavMessage]:
        """Parse one raw line and return zero or more NormalizedNavMessages.

        Must never raise. Return [] on parse error and increment internal error
        counters; the caller will call health() to surface those counts.
        """

    def health(self) -> AdapterHealth:
        return AdapterHealth()

    def reset(self, reason: str = "") -> None:
        pass
