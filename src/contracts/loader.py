"""
Contract Loader — загрузка YAML контрактов с валидацией и hot-reload
"""

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from ..utils.logger import get_logger

logger = get_logger("contracts")


@dataclass
class Contract:
    """Загруженный контракт"""
    path: str
    data: dict
    loaded_at: float
    persona_name: str = ""
    product_names: list = None
    valid: bool = True
    errors: list = None


def load_contract(path: str) -> Contract:
    """
    Загрузить YAML контракт из файла.
    
    Args:
        path: Путь к YAML файлу
    
    Returns:
        Contract объект
    """
    errors = []
    now = time.time()
    
    if not os.path.exists(path):
        return Contract(
            path=path,
            data={},
            loaded_at=now,
            valid=False,
            errors=[f"File not found: {path}"],
        )
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        
        if not isinstance(data, dict):
            return Contract(
                path=path,
                data={},
                loaded_at=now,
                valid=False,
                errors=["Contract must be a YAML mapping (dict)"],
            )
        
        # Валидация
        errors = validate_contract(data)
        
        # Извлекаем метаданные
        persona = data.get("persona", {})
        products = data.get("product", {}).get("products", [])
        
        contract = Contract(
            path=path,
            data=data,
            loaded_at=now,
            persona_name=persona.get("name", "Unknown"),
            product_names=[p.get("name", "?") for p in products],
            valid=len(errors) == 0,
            errors=errors if errors else None,
        )
        
        if contract.valid:
            logger.info(f"Loaded contract: {contract.persona_name} ({path})")
        else:
            logger.warning(f"Contract has errors: {errors}")
        
        return contract
        
    except yaml.YAMLError as e:
        return Contract(
            path=path,
            data={},
            loaded_at=now,
            valid=False,
            errors=[f"YAML parse error: {e}"],
        )
    except Exception as e:
        return Contract(
            path=path,
            data={},
            loaded_at=now,
            valid=False,
            errors=[f"Load error: {e}"],
        )


def validate_contract(data: dict) -> list[str]:
    """
    Валидировать структуру контракта.
    
    Returns:
        Список ошибок (пустой = валиден)
    """
    errors = []
    
    # Обязательные секции
    required_sections = ["persona", "product", "triggers", "conversation_flow"]
    for section in required_sections:
        if section not in data:
            errors.append(f"Missing required section: {section}")
    
    # Persona
    persona = data.get("persona", {})
    if persona:
        if "name" not in persona:
            errors.append("persona.name is required")
        if "backstory" not in persona:
            errors.append("persona.backstory is required")
        
        style = persona.get("speaking_style", {})
        if not style:
            errors.append("persona.speaking_style is required")
        elif "forbidden" not in style:
            errors.append("persona.speaking_style.forbidden is required")
    
    # Product
    if "product" in data:
        product = data.get("product", {})
        if "products" not in product:
            errors.append("product.products is required")
        else:
            for i, p in enumerate(product["products"]):
                if "name" not in p:
                    errors.append(f"product.products[{i}].name is required")
    
    # Triggers
    triggers = data.get("triggers", {})
    if triggers:
        if "respond_to" not in triggers:
            errors.append("triggers.respond_to is required")
        if "ignore" not in triggers:
            errors.append("triggers.ignore is recommended")
    
    # Conversation flow
    flow = data.get("conversation_flow", {})
    if flow:
        if "never" not in flow:
            errors.append("conversation_flow.never is required (what to NEVER do)")
    
    return errors


def reload_if_changed(contract: Contract) -> Contract:
    """
    Перезагрузить контракт если файл изменился (hot-reload).
    
    Returns:
        Новый Contract если файл изменился, иначе тот же
    """
    if not os.path.exists(contract.path):
        return contract
    
    mtime = os.path.getmtime(contract.path)
    if mtime > contract.loaded_at:
        logger.info(f"Contract changed, reloading: {contract.path}")
        return load_contract(contract.path)
    
    return contract


def get_persona_summary(contract: Contract) -> str:
    """Краткая выжимка персонажа для промптов"""
    if not contract.valid:
        return "INVALID CONTRACT"
    
    persona = contract.data.get("persona", {})
    products = contract.data.get("product", {}).get("products", [])
    triggers = contract.data.get("triggers", {}).get("respond_to", [])
    
    parts = [
        f"Имя: {persona.get('name', '?')}",
        f"Роль: {persona.get('backstory', '')[:200]}",
    ]
    
    if products:
        parts.append(f"Продукты: {', '.join(p.get('name', '') for p in products)}")
    
    if triggers:
        topics = [t.get("context", "") for t in triggers]
        parts.append(f"Темы: {', '.join(topics)}")
    
    return "\n".join(parts)
