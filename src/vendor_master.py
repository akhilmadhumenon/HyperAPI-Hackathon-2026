"""
Vendor Master — authoritative list of 35 registered vendors.
Extracted from information.pdf (pages 3-4).
"""

from .models import VendorEntry

# GSTIN state code to state name mapping (Indian states)
GSTIN_STATE_CODES = {
    "01": "Jammu & Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "25": "Daman & Diu",
    "26": "Dadra & Nagar Haveli",
    "27": "Maharashtra",
    "28": "Andhra Pradesh",
    "29": "Karnataka",
    "30": "Goa",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman & Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh (New)",
    "38": "Ladakh",
}


VENDOR_MASTER = [
    VendorEntry(name="Tata Consultancy Services Ltd", gstin="27DNNPH8645X2Z2", state="Maharashtra", bank="HDFC Bank", ifsc="HDFC08433393"),
    VendorEntry(name="Infosys Ltd", gstin="29NVOFQ5021B1Z1", state="Karnataka", bank="ICICI Bank", ifsc="ICIC06799249"),
    VendorEntry(name="Wipro Ltd", gstin="33RUNFF9840I2ZX", state="Tamil Nadu", bank="State Bank of India", ifsc="SBIN04586110"),
    VendorEntry(name="HCL Technologies Ltd", gstin="07KVNCM0180L2ZB", state="Delhi", bank="Axis Bank", ifsc="UTIB09581543"),
    VendorEntry(name="Tech Mahindra Ltd", gstin="36EHNM3324Y2ZS", state="Telangana", bank="Kotak Mahindra Bank", ifsc="KKBK02020043"),
    VendorEntry(name="HDFC Bank Ltd", gstin="24JUQPQ3509F2ZQ", state="Gujarat", bank="Punjab National Bank", ifsc="PUNB00230598"),
    VendorEntry(name="ICICI Bank Ltd", gstin="27ISTPJ7395K1Z9", state="Maharashtra", bank="Bank of Baroda", ifsc="BARB03660533"),
    VendorEntry(name="Axis Bank Ltd", gstin="29QYNHK6736A1Z4", state="Karnataka", bank="Canara Bank", ifsc="CNRB03992832"),
    VendorEntry(name="Kotak Mahindra Bank Ltd", gstin="33ZAFAJ5939Q1Z6", state="Tamil Nadu", bank="Union Bank of India", ifsc="UBIN05712539"),
    VendorEntry(name="Larsen & Toubro Ltd", gstin="07JJFCT6194X1ZY", state="Delhi", bank="IndusInd Bank", ifsc="INDB05864507"),
    VendorEntry(name="Reliance Industries Ltd", gstin="27UJEAV7431I2ZQ", state="Maharashtra", bank="HDFC Bank", ifsc="HDFC02563534"),
    VendorEntry(name="Mahindra & Mahindra Ltd", gstin="29MXIPY2182A1Z3", state="Karnataka", bank="ICICI Bank", ifsc="ICIC09199267"),
    VendorEntry(name="Bajaj Auto Ltd", gstin="33MAVHY4357J1ZD", state="Tamil Nadu", bank="State Bank of India", ifsc="SBIN08968954"),
    VendorEntry(name="Maruti Suzuki India Ltd", gstin="36MOVHL9365E1ZJ", state="Telangana", bank="Axis Bank", ifsc="UTIB02281961"),
    VendorEntry(name="Hindustan Unilever Ltd", gstin="24VSVHY0580V2ZM", state="Gujarat", bank="Kotak Mahindra Bank", ifsc="KKBK08743237"),
    VendorEntry(name="ITC Ltd", gstin="19VMDCR2336B2ZQ", state="West Bengal", bank="Punjab National Bank", ifsc="PUNB00205095"),
    VendorEntry(name="Britannia Industries Ltd", gstin="27XRKAK4454Y1Z4", state="Maharashtra", bank="Bank of Baroda", ifsc="BARB04125247"),
    VendorEntry(name="Godrej Consumer Products Ltd", gstin="29XCEPG7671O1ZW", state="Karnataka", bank="Canara Bank", ifsc="CNRB09690062"),
    VendorEntry(name="Dabur India Ltd", gstin="27REGAG5326F2ZF", state="Maharashtra", bank="Union Bank of India", ifsc="UBIN07846634"),
    VendorEntry(name="Marico Ltd", gstin="33HNZHX1406Y1Z6", state="Tamil Nadu", bank="IndusInd Bank", ifsc="INDB02421605"),
    VendorEntry(name="Siemens Ltd", gstin="07STKHF8213R2ZN", state="Delhi", bank="HDFC Bank", ifsc="HDFC04137838"),
    VendorEntry(name="ABB India Ltd", gstin="29ZTPN5383Z1Z9", state="Karnataka", bank="ICICI Bank", ifsc="ICIC04391535"),
    VendorEntry(name="Bosch Ltd", gstin="27WIEPL2499R2ZI", state="Maharashtra", bank="State Bank of India", ifsc="SBIN04300162"),
    VendorEntry(name="Cummins India Ltd", gstin="36GVNAN2524N2ZN", state="Telangana", bank="Axis Bank", ifsc="UTIB06015679"),
    VendorEntry(name="Bharat Electronics Ltd", gstin="24KBAHR9713I2ZT", state="Gujarat", bank="Kotak Mahindra Bank", ifsc="KKBK09951656"),
    VendorEntry(name="Mphasis Ltd", gstin="27YKDAF6709N2Z2", state="Maharashtra", bank="Punjab National Bank", ifsc="PUNB02751376"),
    VendorEntry(name="Mindtree Ltd", gstin="29UDDAI6354G1ZL", state="Karnataka", bank="Bank of Baroda", ifsc="BARB09593974"),
    VendorEntry(name="L&T Infotech Ltd", gstin="33CFFAA0308D2Z5", state="Tamil Nadu", bank="Canara Bank", ifsc="CNRB05466068"),
    VendorEntry(name="Coforge Ltd", gstin="07YGGCU2946D1Z7", state="Delhi", bank="Union Bank of India", ifsc="UBIN07334444"),
    VendorEntry(name="Persistent Systems Ltd", gstin="27ESFHT4802X2ZF", state="Maharashtra", bank="IndusInd Bank", ifsc="INDB03543062"),
    VendorEntry(name="Zensar Technologies Ltd", gstin="29DUKCM9645A1ZE", state="Karnataka", bank="HDFC Bank", ifsc="HDFC00009407"),
    VendorEntry(name="Cyient Ltd", gstin="33GGBHA6001G2ZF", state="Tamil Nadu", bank="ICICI Bank", ifsc="ICIC00814501"),
    VendorEntry(name="KPIT Technologies Ltd", gstin="36PZPV4835K1Z1", state="Telangana", bank="State Bank of India", ifsc="SBIN08193427"),
    VendorEntry(name="Tata Elxsi Ltd", gstin="24YSICA7112O1ZX", state="Gujarat", bank="Axis Bank", ifsc="UTIB06423458"),
    VendorEntry(name="Happiest Minds Technologies Ltd", gstin="27EEYFB4015Y2ZV", state="Maharashtra", bank="Kotak Mahindra Bank", ifsc="KKBK03087179"),
]

# Build lookup dictionaries
VENDOR_BY_NAME = {v.name.lower(): v for v in VENDOR_MASTER}
VENDOR_BY_GSTIN = {v.gstin: v for v in VENDOR_MASTER}
VENDOR_BY_IFSC = {v.ifsc: v for v in VENDOR_MASTER}
VENDOR_NAMES = [v.name for v in VENDOR_MASTER]
VENDOR_NAMES_LOWER = [v.name.lower() for v in VENDOR_MASTER]


def get_state_from_gstin(gstin: str) -> str:
    """Extract state from GSTIN (first 2 digits are state code)."""
    if len(gstin) >= 2:
        code = gstin[:2]
        return GSTIN_STATE_CODES.get(code, "Unknown")
    return "Unknown"
