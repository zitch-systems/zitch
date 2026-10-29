"""Parsers for the supplied VAS PDF and Postman contracts.

Catalog fields are data, never arbitrary request keys. Receipt descriptions are
only inspected for the documented token/units labels after confirmed success.
"""
import re
from decimal import Decimal, InvalidOperation

from .client import ProviderError, money

INPUT_NAMES = {"customerId", "amount", "itemId", "firstname", "lastname"}


def text_value(value, *, maximum=160):
    if (not isinstance(value, str) or len(value.strip()) > maximum
            or any(ord(char) < 32 for char in value)):
        raise ValueError("Enter a valid biller field")
    return value.strip()


def fields_schema(rows):
    """Validate the bank's supported fields before any customer authorization."""
    if not isinstance(rows, list) or not 1 <= len(rows) <= len(INPUT_NAMES):
        raise ProviderError("invalid_biller_fields")
    result = {}
    try:
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError
            name = row.get("fieldName")
            if name not in INPUT_NAMES or name in result:
                raise ValueError
            validation = row.get("validation", "").upper()
            select = row.get("isSelectData", "N").upper()
            if validation not in {"Y", "N"} or select not in {"Y", "N"}:
                raise ValueError
            field = {"fieldName": name, "fieldDescription": text_value(row.get("fieldDescription", name)),
                     "validation": validation, "isSelectData": select}
            if select == "Y":
                # The supplied contract defines itemId as the selection value.
                if name != "itemId" or not isinstance(row.get("items"), list) or not row["items"]:
                    raise ValueError
                items, seen = [], set()
                for item in row["items"]:
                    item_id = item.get("itemId")
                    if (not isinstance(item_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", item_id)
                            or item_id in seen):
                        raise ValueError
                    seen.add(item_id)
                    label = text_value(item.get("itemName"))
                    if not label:
                        raise ValueError
                    option = {"itemId": item_id, "itemName": label}
                    # Electricity's PREPAID/POSTPAID example omits amount.
                    if "amount" in item:
                        value = item["amount"]
                        if isinstance(value, bool):
                            raise ValueError
                        price = Decimal(str(value))
                        option["amount"] = "0.00" if price == 0 else money(value)
                    items.append(option)
                field["items"] = items
            elif name == "itemId" or row.get("items"):
                raise ValueError
            result[name] = field
        if "customerId" not in result:
            raise ValueError
    except (ValueError, TypeError, AttributeError, InvalidOperation):
        raise ProviderError("invalid_biller_fields") from None
    return result


def selection_amount(fields, item, amount):
    submitted = money(amount)
    selection = fields.get("itemId")
    if selection:
        matches = [row for row in selection["items"] if row["itemId"] == item]
        if len(matches) != 1:
            raise ValueError("Choose an available biller item")
        price = matches[0].get("amount", "0.00")
        if Decimal(price) > 0:
            if price != submitted:
                raise ValueError("The bill price changed. Refresh and try again")
            return Decimal(submitted)
    elif item:
        raise ValueError("This biller does not require a plan")
    if "amount" not in fields:
        raise ProviderError("biller_amount_unconfirmed")
    return Decimal(submitted)


def receipt(data, *, query=False):
    """Extract purchased value, not credentials or unstructured provider text."""
    result = {}
    token = data.get("token")
    if data.get("isToken") is True and isinstance(token, str) and 0 < len(token.strip()) <= 256:
        if not any(ord(char) < 32 for char in token):
            result["token"] = token.strip()
    # Immediate electricity responses carry units in otherField; status
    # responses carry them in description. Only expose the documented label.
    unit_sources = [data.get("otherField")]
    description = data.get("description")
    if query and data.get("isToken", True) is True and isinstance(description, str) and len(description) <= 4096:
        # The status sample has no data.token: "Token:\n<20 digits>".
        tokens = re.findall(r"(?im)^Token:[ \t]*(?:\r?\n[ \t]*)?([0-9 -]+)[ \t]*$", description)
        tokens = {re.sub(r"[ -]", "", value) for value in tokens}
        if len(tokens) == 1 and re.fullmatch(r"[0-9]{20}", next(iter(tokens))):
            parsed = next(iter(tokens))
            if not result.get("token") or result["token"] == parsed:
                result["token"] = parsed
            else:
                result.pop("token", None)
        elif tokens:
            result.pop("token", None)
        unit_sources.append(description)
    units = set()
    for source in unit_sources:
        if isinstance(source, str) and len(source) <= 4096:
            units.update(re.findall(r"(?im)^Units Purchased:[ \t]*([0-9]+(?:\.[0-9]+)?(?:[ \t]+kWh)?)[ \t]*\r?$", source))
    if len(units) == 1 and len(next(iter(units))) <= 40:
        result["electricity_units"] = next(iter(units))
    return result
