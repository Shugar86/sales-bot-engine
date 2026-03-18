"""Tests for Contract Loader + Validator"""
import pytest
import yaml
import os
from src.contracts.loader import load_contract, validate_contract, reload_if_changed, get_persona_summary


@pytest.fixture
def valid_contract_data():
    return {
        "persona": {
            "name": "Андрей",
            "backstory": "Бывший кинолог-инструктор, 12 лет опыта",
            "speaking_style": {
                "tone": "Бывалый",
                "patterns": ["Короткие предложения"],
                "forbidden": ["Эмодзи-спам", "Маркетинг"],
            }
        },
        "product": {
            "products": [
                {"name": "Корм Проф Спорт", "description": "Для рабочих собак"}
            ]
        },
        "triggers": {
            "respond_to": [
                {"context": "Кормление", "keywords": ["корм"]}
            ],
            "ignore": ["Политика"],
        },
        "conversation_flow": {
            "group_chat": {"steps": ["Встрять репликой"]},
            "never": ["Спамить"],
        }
    }


@pytest.fixture
def contract_file(tmp_path, valid_contract_data):
    path = tmp_path / "persona.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(valid_contract_data, f, allow_unicode=True)
    return str(path)


class TestContractLoading:
    
    def test_load_valid_contract(self, contract_file):
        contract = load_contract(contract_file)
        
        assert contract.valid is True
        assert contract.persona_name == "Андрей"
        assert "Корм Проф Спорт" in contract.product_names
        assert contract.errors is None
    
    def test_load_missing_file(self):
        contract = load_contract("nonexistent.yaml")
        
        assert contract.valid is False
        assert "not found" in str(contract.errors).lower()
    
    def test_load_invalid_yaml(self, tmp_path):
        path = tmp_path / "bad.yaml"
        with open(path, "w") as f:
            f.write("{broken: yaml: [[[}")
        
        contract = load_contract(str(path))
        assert contract.valid is False


class TestContractValidation:
    
    def test_valid_passes(self, valid_contract_data):
        errors = validate_contract(valid_contract_data)
        assert errors == []
    
    def test_missing_persona(self):
        errors = validate_contract({"product": {}, "triggers": {}, "conversation_flow": {}})
        assert any("persona" in e for e in errors)
    
    def test_missing_persona_name(self, valid_contract_data):
        del valid_contract_data["persona"]["name"]
        errors = validate_contract(valid_contract_data)
        assert any("persona.name" in e for e in errors)
    
    def test_missing_forbidden(self, valid_contract_data):
        del valid_contract_data["persona"]["speaking_style"]["forbidden"]
        errors = validate_contract(valid_contract_data)
        assert any("forbidden" in e for e in errors)
    
    def test_missing_never(self, valid_contract_data):
        del valid_contract_data["conversation_flow"]["never"]
        errors = validate_contract(valid_contract_data)
        assert any("never" in e for e in errors)
    
    def test_missing_products(self, valid_contract_data):
        del valid_contract_data["product"]["products"]
        errors = validate_contract(valid_contract_data)
        assert any("products" in e for e in errors)


class TestHotReload:
    
    def test_reload_when_changed(self, contract_file):
        import time
        
        contract1 = load_contract(contract_file)
        original_name = contract1.persona_name
        
        # Изменяем файл
        time.sleep(0.1)
        with open(contract_file, "r") as f:
            data = yaml.safe_load(f)
        data["persona"]["name"] = "Борис"
        with open(contract_file, "w") as f:
            yaml.dump(data, f, allow_unicode=True)
        
        contract2 = reload_if_changed(contract1)
        
        assert contract2.persona_name == "Борис"
        assert contract2.persona_name != original_name
    
    def test_no_reload_when_unchanged(self, contract_file):
        contract1 = load_contract(contract_file)
        contract2 = reload_if_changed(contract1)
        
        assert contract2 is contract1  # Same object


class TestPersonaSummary:
    
    def test_summary_contains_key_info(self, contract_file):
        contract = load_contract(contract_file)
        summary = get_persona_summary(contract)
        
        assert "Андрей" in summary
        assert "кинолог" in summary.lower() or "Корм" in summary
