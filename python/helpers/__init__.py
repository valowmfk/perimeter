"""
Perimeter automation helpers package.
"""

from .dns_manager import dns_add_record, dns_remove_record, dns_add_cname, dns_remove_cname

__all__ = ["dns_add_record", "dns_remove_record", "dns_add_cname", "dns_remove_cname"]
