import enum


class ServiceCategory(str, enum.Enum):
    """Matches IServiceItem['category'] in data/appData.tsx"""
    registration = "registration"
    compliance = "compliance"
    licenses = "licenses"


class ServiceType(str, enum.Enum):
    """Matches servicesGridData ids/labels in data/appData.tsx"""
    # registration
    business_ntn = "Business NTN"
    simple_ntn_registration = "Simple NTN Registration"
    business_registration = "Business Registration"
    company_registration = "Company Registration"
    filer_registration = "Filer Registration"
    gst_registration = "GST Registration"

    # compliance
    tax_return_filing = "Tax Return Filing"
    fbr_notices = "FBR Notices"
    wealth_statement = "Wealth Statement"
    dts_registration = "DTS Registration"

    # licenses
    imp_exp_license_psw = "Imp & Exp License (PSW)"
    trade_mark_registration = "Trade Mark Registration"
    pec_registration = "PEC Registration"
    chamber_membership = "Chamber Membership"
    pseb = "PSEB"
    dnfbp = "DNFBP"

    other = "Other"


class ModuleAccess(str, enum.Enum):
    """Access levels for module operations"""
    READ = "read"
    UPDATE = "update"
    ALL = "all"

class ExpenseType(str, enum.Enum):
    expense = "Expense"
    income = "income"
    debt = "debt"


class ExpenseCategory(str, enum.Enum):
    utilities = "Utilities"
    food = "Food"
    transport = "Transport"
    rent = "rent"
    business = "Business"
    other = "Other"


class ExpenseCategoryColor(str, enum.Enum):
    Red = "#FF5733" 
    Green = "#33FF57"      
    Blue = "#3357FF"
    Yellow = "#F1C40F"    
    Purple = "#8E44AD"  
    Gray = "#95A5A6"     


class ExpenseAccountType(str, enum.Enum):
    personal = "Personal Card"
    business = "Business Card"
    debit = "Debit Card"



class CardColor(str, enum.Enum):
    Primary = "#b51632"
    Secondary = "#4ab898"
    Dark = "#474f5a"


class DebtCategory(str, enum.Enum):
    DebtToTake = "Debt to Take"
    DebtToGive = "Debt to Give"