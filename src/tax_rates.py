"""
HSN/SAC code to GST rate mapping for Indian taxation.
Used to detect wrong_tax_rate needles.
"""

# Common HSN/SAC to GST rate mappings
# HSN = Harmonized System of Nomenclature (goods)
# SAC = Services Accounting Code (services)

HSN_SAC_TAX_RATES = {
    # Transport / Freight services - 5%
    "9965": 5, "996511": 5, "996512": 5, "996513": 5, "996519": 5,
    "996521": 5, "996531": 5,
    
    # Restaurant services - 5%
    "9963": 5, "996331": 5,
    
    # Accommodation / Hotel (tariff < 1000) - 12%
    "996311": 12,
    
    # Accommodation / Hotel (tariff 1000-7500) - 12%  
    "996312": 12,
    
    # IT / Software services - 18%
    "998311": 18, "998312": 18, "998313": 18, "998314": 18,
    "9983": 18, "998339": 18,
    
    # Consulting / Professional services - 18%
    "9982": 18, "998211": 18, "998212": 18, "998213": 18, "998214": 18,
    "998215": 18, "998216": 18, "998221": 18, "998222": 18, "998231": 18,
    
    # Management consulting - 18%
    "9983": 18, "998311": 18, "998312": 18,
    
    # Engineering services - 18%
    "9981": 18, "998311": 18,
    
    # Legal services - 18%
    "9982": 18,
    
    # Accounting / Auditing services - 18%
    "9982": 18, "998221": 18, "998222": 18,
    
    # Advertising services - 18%
    "9983": 18,
    
    # Manpower supply - 18%
    "9985": 18, "998511": 18, "998512": 18, "998513": 18,
    "998514": 18, "998515": 18,
    
    # Renting of immovable property - 18%
    "9972": 18, "997211": 18, "997212": 18,
    
    # Security services - 18%
    "9985": 18, "998515": 18,
    
    # Maintenance & Repair - 18%
    "9987": 18, "998711": 18, "998712": 18, "998713": 18, "998714": 18,
    "998715": 18, "998716": 18, "998717": 18, "998719": 18,
    "998721": 18, "998722": 18, "998723": 18, "998724": 18, "998725": 18,
    
    # Telecom services - 18%
    "9984": 18, "998411": 18, "998412": 18, "998413": 18, "998414": 18,
    
    # Insurance services - 18%
    "9971": 18, "997111": 18, "997112": 18, "997113": 18, "997114": 18,
    
    # Banking/Financial services - 18%
    "9971": 18, "997111": 18, "997112": 18,
    
    # Printing services - 12% or 18%
    "9989": 18, "998912": 18,
    
    # Construction services - 12%/18%
    "9954": 18, "995411": 12, "995412": 12, "995421": 18, "995422": 18,
    
    # Goods HSN codes
    # Computer hardware - 18%
    "8471": 18, "8473": 18,
    
    # Office supplies / stationery - 12%/18%
    "4820": 12, "4802": 12,
    "8472": 18,
    
    # Furniture - 12%/18%
    "9401": 18, "9403": 18, "9404": 18,
    
    # Electrical equipment - 18%/28%
    "8504": 18, "8536": 18, "8537": 18,
    
    # Motor vehicles - 28%
    "8703": 28,
    
    # Air conditioners - 28%
    "8415": 28,
    
    # Food items - 5%
    "1905": 5, "0402": 5, "2106": 18,
    
    # Cement - 28%
    "2523": 28,
    
    # Iron & Steel - 18%
    "7210": 18, "7216": 18, "7308": 18,
    
    # Petroleum products (not under GST mostly, but if included) - 5%
    "2710": 5,
    
    # Cleaning supplies - 18%
    "3402": 18, "3401": 18,
    
    # Paper products - 12%
    "4802": 12, "4811": 12, "4819": 12, "4821": 12,
    
    # Cables - 18%/28%
    "8544": 18,
    
    # Software (packaged) - 18%
    "8523": 18,
}

# Broader category mapping (first 2 or 4 digits)
HSN_SAC_CATEGORY_RATES = {
    # Services starting with 99
    "9965": 5,   # Transport/Freight
    "9963": 5,   # Restaurant
    "9971": 18,  # Financial/Insurance
    "9972": 18,  # Real estate
    "9973": 18,  # Leasing
    "9981": 18,  # R&D
    "9982": 18,  # Legal/Accounting
    "9983": 18,  # IT/Consulting
    "9984": 18,  # Telecom
    "9985": 18,  # Support/Manpower
    "9986": 18,  # Support services
    "9987": 18,  # Maintenance
    "9988": 18,  # Manufacturing
    "9989": 18,  # Other services
    "9954": 18,  # Construction
    "9964": 5,   # Postal/courier
    "9966": 5,   # Terminal services
    "4901": 0,   # Books (Exempt)
    "9967": 18,  # Support transport
    "9968": 18,  # Postal courier
    "9969": 18,  # General
    "9991": 0,   # Government
    "9992": 0,   # Education
    "9993": 0,   # Health
    "9994": 18,  # Recreation/Cultural
    "9995": 18,  # Other services
    "9996": 12,  # Hotel (composite)
    "9997": 18,  # Other services
}


def get_expected_tax_rate(hsn_sac: str) -> float:
    """
    Get the expected GST rate for a given HSN/SAC code.
    Tries exact match first, then prefix matches.
    Returns -1 if no mapping found.
    """
    hsn_sac = hsn_sac.strip()
    
    # Direct lookup
    if hsn_sac in HSN_SAC_TAX_RATES:
        return HSN_SAC_TAX_RATES[hsn_sac]
    
    # Try 4-digit prefix
    if len(hsn_sac) >= 4:
        prefix4 = hsn_sac[:4]
        if prefix4 in HSN_SAC_TAX_RATES:
            return HSN_SAC_TAX_RATES[prefix4]
        if prefix4 in HSN_SAC_CATEGORY_RATES:
            return HSN_SAC_CATEGORY_RATES[prefix4]
    
    # Try 2-digit prefix for goods
    if len(hsn_sac) >= 2:
        prefix2 = hsn_sac[:2]
        # Most goods chapters
        goods_18 = {"84", "85", "39", "40", "73", "76", "34", "38"}
        goods_28 = {"87"}
        goods_12 = {"48", "44", "19"}
        goods_5 = {"27", "09", "10", "11"}
        
        if prefix2 in goods_28:
            return 28
        if prefix2 in goods_18:
            return 18
        if prefix2 in goods_12:
            return 12
        if prefix2 in goods_5:
            return 5
    
    return -1
