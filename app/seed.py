"""Starter catalogue, sample customers, contract prices, tax rates and sample quote requests.

Replace with your real product/customer sync. Every customer created here is marked `is_sample`, and the
app refuses to email sample customers through Gmail, Outlook or n8n (see QuoteService), so the realistic
addresses below can never receive a real email by accident.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal as D
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Customer, CustomerPrice, InventoryItem, PriceTier, Product, Quote, TaxRate, utcnow
from .schemas import InboundEmail

if TYPE_CHECKING:
    from .service import QuoteService

# Statewide base sales tax rates only; local and county taxes are not included.
TAX_RATES = [
    ("US-AZ", "Arizona (state rate)", "0.0560"),
    ("US-CA", "California (state rate)", "0.0725"),
    ("US-CO", "Colorado (state rate)", "0.0290"),
    ("US-FL", "Florida (state rate)", "0.0600"),
    ("US-GA", "Georgia (state rate)", "0.0400"),
    ("US-IL", "Illinois (state rate)", "0.0625"),
    ("US-MA", "Massachusetts (state rate)", "0.0625"),
    ("US-MN", "Minnesota (state rate)", "0.0688"),
    ("US-NC", "North Carolina (state rate)", "0.0475"),
    ("US-NV", "Nevada (state rate)", "0.0685"),
    ("US-NY", "New York (state rate)", "0.0400"),
    ("US-OH", "Ohio (state rate)", "0.0575"),
    ("US-OR", "Oregon (no sales tax)", "0"),
    ("US-PA", "Pennsylvania (state rate)", "0.0600"),
    ("US-TX", "Texas (state rate)", "0.0625"),
    ("US-WA", "Washington (state rate)", "0.0650"),
]


def _customer(name, company, email, tax_region, discount_pct, payment_terms, billing_address):
    return dict(name=name, company=company, email=email, tax_region=tax_region, discount_pct=discount_pct,
                payment_terms=payment_terms, billing_address=billing_address)


CUSTOMERS = [
    _customer("Jordan Lee", "Brightline Construction", "jordan.lee@brightline-construction.com", "US-CA", "5", "Net 30",
              "455 Market Street, Suite 900\nSan Francisco, CA 94105"),
    _customer("Priya Shah", "Northwind Facilities", "priya@northwindfacilities.com", "US-TX", "10", "Net 45",
              "2100 Ross Avenue\nDallas, TX 75201"),
    _customer("Maria Gonzalez", "Summit Ridge Builders", "mgonzalez@summitridgebuilders.com", "US-CO", "7", "Net 30",
              "4801 Brighton Blvd, Suite 210\nDenver, CO 80216"),
    _customer("David Chen", "Pacific Coast Electrical Contractors", "david.chen@pacificcoastelectric.com", "US-WA", "8", "Net 30",
              "3200 1st Avenue S, Suite 400\nSeattle, WA 98134"),
    _customer("Aisha Rahman", "Lakeside Facility Services", "arahman@lakesidefacilityservices.com", "US-IL", "5", "Net 45",
              "1901 W Fulton Market\nChicago, IL 60612"),
    _customer("Michael O'Brien", "Keystone Mechanical Group", "mobrien@keystonemechanicalgroup.com", "US-PA", "3", "Net 30",
              "2100 E Allegheny Ave\nPhiladelphia, PA 19134"),
    _customer("Sofia Rossi", "Sunbelt Colocation", "s.rossi@sunbeltcolo.com", "US-AZ", "10", "Net 60",
              "615 N 48th St\nPhoenix, AZ 85008"),
    _customer("James Carter", "Peachtree Property Management", "jcarter@peachtreepm.com", "US-GA", "4", "Net 30",
              "1100 Spring St NW, Suite 300\nAtlanta, GA 30309"),
    _customer("Emily Nguyen", "Harborview Health Facilities", "enguyen@harborviewhealth.com", "US-MA", "6", "Net 45",
              "150 Northern Ave\nBoston, MA 02210"),
    _customer("Carlos Mendoza", "Desert Sun General Contracting", "carlos@desertsungc.com", "US-NV", "5", "Net 30",
              "3960 W Russell Rd\nLas Vegas, NV 89118"),
    _customer("Hannah Schmidt", "North Star Precision Manufacturing", "hschmidt@northstarprecision.com", "US-MN", "8", "Net 45",
              "7600 Boone Ave N\nBrooklyn Park, MN 55428"),
    _customer("Robert Williams", "Triangle Network Services", "rwilliams@trianglenetworkservices.com", "US-NC", "6", "Net 30",
              "5000 Falls of Neuse Rd, Suite 108\nRaleigh, NC 27609"),
    _customer("Olivia Brown", "Buckeye Industrial Distributors", "obrown@buckeyeindustrialdist.com", "US-OH", "12", "Net 60",
              "2250 Westbelt Dr\nColumbus, OH 43228"),
    _customer("Daniel Kim", "Golden Gate Renovation Co.", "daniel@goldengaterenovation.com", "US-CA", "5", "Net 30",
              "1600 Bryant St\nSan Francisco, CA 94103"),
    _customer("Fatima Ali", "Gulf Coast Marine Services", "fali@gulfcoastmarineservices.com", "US-FL", "7", "Net 30",
              "1901 Guy N Verger Blvd\nTampa, FL 33605"),
    _customer("Thomas Anderson", "Lone Star Structured Cabling", "tanderson@lonestarcabling.com", "US-TX", "9", "Net 45",
              "7155 Old Katy Rd, Suite 250\nHouston, TX 77024"),
    _customer("Grace Park", "Empire Workplace Interiors", "grace.park@empireworkplace.com", "US-NY", "4", "Net 30",
              "236 W 30th St, Floor 5\nNew York, NY 10001"),
    _customer("Ahmed Hassan", "Cascade Utility Services", "ahassan@cascadeutilityservices.com", "US-OR", "6", "Net 30",
              "5200 NE Columbia Blvd\nPortland, OR 97218"),
    _customer("Laura Martinez", "Redwood Solar Installations", "laura.martinez@redwoodsolarinc.com", "US-CA", "8", "Net 30",
              "2700 Alvarado St, Suite B\nSan Leandro, CA 94577"),
    _customer("Kevin Patel", "Metro Fleet Maintenance", "kpatel@metrofleetmaintenance.com", "US-IL", "10", "Net 60",
              "4300 S Racine Ave\nChicago, IL 60609"),
    _customer("Rachel Cohen", "Silverline Event Production", "rachel@silverlineevents.com", "US-NV", "3", "Prepayment",
              "6435 S Valley View Blvd\nLas Vegas, NV 89118"),
    _customer("Samuel Okafor", "Brightwater Unified School District", "sokafor@brightwaterusd.org", "US-GA", "5", "Net 45",
              "1250 Brightwater Pkwy\nMarietta, GA 30067"),
]

# sku, name, aliases, unit, price, weight kg, tiers [(min_qty, price)], (on_hand, in-stock days, backorder days)
PRODUCTS = [
    ("FS-HB-M8-50", "Hex Bolt M8 x 50 mm, Zinc Plated (box of 100)", ["m8 bolts", "m8x50 hex bolt"], "box", "18.50", "1.6", [(10, "16.90"), (50, "15.20")], (120, 2, 10)),
    ("FS-HN-M8", "Hex Nut M8, Zinc Plated (box of 100)", ["m8 nuts", "m8 hex nut"], "box", "6.40", "0.5", [(10, "5.80"), (50, "5.20")], (300, 2, 10)),
    ("FS-WA-M8", "Flat Washer M8, Zinc Plated (box of 100)", ["m8 washers"], "box", "3.90", "0.3", [(10, "3.50")], (200, 2, 10)),
    ("EL-C6-305", "Cat6 UTP Cable, 305 m Box, Blue", ["cat 6 cable", "ethernet cable 305m"], "box", "129.00", "10.5", [(5, "119.00"), (20, "109.00")], (35, 3, 14)),
    ("EL-C6A-305", "Cat6A U/FTP Cable, 305 m Box, Grey", ["cat 6a cable", "shielded cat6a cable"], "box", "219.00", "16.0", [(5, "205.00")], (12, 3, 14)),
    ("EL-PP-24-C6", "Patch Panel 24-Port Cat6, 1U", ["24 port patch panel"], "ea", "49.00", "1.8", [(10, "44.00")], (60, 2, 12)),
    ("SF-GL-NIT-L", "Nitrile Gloves, Size L (box of 100)", ["large nitrile gloves"], "box", "11.50", "0.6", [(20, "10.20"), (100, "9.40")], (40, 1, 7)),
    ("SF-GL-NIT-M", "Nitrile Gloves, Size M (box of 100)", ["medium nitrile gloves"], "box", "11.50", "0.6", [(20, "10.20"), (100, "9.40")], (400, 1, 7)),
    ("SF-HH-WHT", "Safety Helmet, White, EN397", ["hard hat", "white hard hat"], "ea", "14.90", "0.4", [(25, "12.90")], (150, 2, 10)),
    ("SF-VEST-HV-L", "Hi-Vis Safety Vest, Size L", ["hi vis vest large"], "ea", "6.20", "0.15", [(50, "5.40")], (80, 2, 10)),
    ("SF-VEST-HV-XL", "Hi-Vis Safety Vest, Size XL", ["hi vis vest extra large"], "ea", "6.20", "0.16", [(50, "5.40")], (60, 2, 10)),
    ("TL-DR-18V", "Cordless Drill 18V with 2 Batteries", ["cordless drill", "18v drill"], "ea", "169.00", "2.4", [(10, "155.00")], (0, 3, 21)),
    ("TL-BIT-HSS-19", "HSS Drill Bit Set, 19 pcs", ["drill bit set"], "set", "24.50", "0.7", [(10, "21.90")], (45, 2, 10)),
]

# customer email, sku, min qty, contract unit price, note
CUSTOMER_PRICES = [
    ("jordan.lee@brightline-construction.com", "SF-HH-WHT", 1, "11.50", "2026 annual pricing agreement"),
    ("jordan.lee@brightline-construction.com", "EL-C6-305", 1, "112.00", "2026 annual pricing agreement"),
    ("priya@northwindfacilities.com", "SF-GL-NIT-M", 50, "8.90", "Volume agreement, 50+ boxes"),
    ("mgonzalez@summitridgebuilders.com", "SF-HH-WHT", 1, "12.20", "Aspen Ridge project pricing"),
    ("mgonzalez@summitridgebuilders.com", "FS-HB-M8-50", 50, "14.50", "Aspen Ridge project pricing"),
    ("david.chen@pacificcoastelectric.com", "EL-C6-305", 1, "110.00", "Preferred contractor agreement"),
    ("david.chen@pacificcoastelectric.com", "EL-PP-24-C6", 1, "42.00", "Preferred contractor agreement"),
    ("s.rossi@sunbeltcolo.com", "EL-C6A-305", 1, "199.00", "Framework agreement FY2026"),
    ("s.rossi@sunbeltcolo.com", "EL-PP-24-C6", 10, "40.00", "Framework agreement FY2026"),
    ("enguyen@harborviewhealth.com", "SF-GL-NIT-M", 1, "9.60", "GPO contract pricing"),
    ("enguyen@harborviewhealth.com", "SF-GL-NIT-L", 1, "9.60", "GPO contract pricing"),
    ("obrown@buckeyeindustrialdist.com", "FS-HN-M8", 1, "5.00", "Distributor price list 2026"),
    ("obrown@buckeyeindustrialdist.com", "FS-WA-M8", 1, "3.20", "Distributor price list 2026"),
    ("tanderson@lonestarcabling.com", "EL-C6-305", 5, "108.00", "Volume agreement, 5+ boxes"),
    ("laura.martinez@redwoodsolarinc.com", "TL-DR-18V", 5, "152.00", "Fleet tool agreement"),
    ("kpatel@metrofleetmaintenance.com", "TL-DR-18V", 1, "149.00", "Municipal contract 2026"),
    ("kpatel@metrofleetmaintenance.com", "TL-BIT-HSS-19", 1, "20.50", "Municipal contract 2026"),
]


def _request(email, name, subject, greeting, intro, items, ship_to, closing, signature, delivery=None, bullet="-"):
    return dict(email=email, name=name, subject=subject, greeting=greeting, intro=intro, items=items, ship_to=ship_to,
                closing=closing, signature=signature, delivery=delivery, bullet=bullet)


# One quote request email per sample customer (see seed_demo_quotes).
DEMO_REQUESTS = [
    _request("mgonzalez@summitridgebuilders.com", "Maria Gonzalez", "RFQ – Aspen Ridge Townhomes Phase 2", "Hi team,",
             "We're kicking off Phase 2 at Aspen Ridge next month. Could you put together pricing for the following?",
             ["30 x hard hats (white)", "FS-HB-M8-50 x 60", "60 boxes M8 hex nuts", "20 x hi vis vest XL"],
             "Please ship to 88 Aspen Ridge Road, Denver, CO 80216 (site office).", "Thanks in advance,",
             ["Maria Gonzalez", "Project Manager | Summit Ridge Builders"], delivery="We'd need delivery by October 10."),
    _request("david.chen@pacificcoastelectric.com", "David Chen", "Pricing request – Cat6 rollout", "Hi,",
             "We're rewiring two office floors for a client in SoDo and need pricing on:",
             ["Cat6 cable 305m box x 12", "10 x 24 port patch panel", "5 sets drill bit set"],
             "Ship to 3200 1st Avenue S, Seattle, WA 98134 (warehouse, dock 3).", "Thanks,",
             ["David Chen", "Estimator, Pacific Coast Electrical Contractors"], bullet="•"),
    _request("arahman@lakesidefacilityservices.com", "Aisha Rahman", "October safety supplies", "Hello,",
             "Please quote our monthly safety supply order for the Fulton Market campus:",
             ["100 boxes nitrile gloves", "40 x hi vis vest large", "25 x hard hats (white)"],
             "Ship to 1901 W Fulton Market, Chicago, IL 60612 (receiving door B).", "Kind regards,",
             ["Aisha Rahman", "Facilities Coordinator", "Lakeside Facility Services"], delivery="We need delivery by September 30."),
    _request("mobrien@keystonemechanicalgroup.com", "Michael O'Brien", "Fastener stock – quote request", "Hi,",
             "Can you price the following fasteners for our plant maintenance stock? Part numbers from your catalog below.",
             ["FS-HB-M8-50 x 120", "FS-HN-M8 x 120", "FS-WA-M8 x 240"],
             "Ship to 2100 E Allegheny Ave, Philadelphia, PA 19134.", "Thanks,",
             ["Mike O'Brien", "Maintenance Supervisor, Keystone Mechanical Group"], bullet="1."),
    _request("s.rossi@sunbeltcolo.com", "Sofia Rossi", "Hall B fit-out – Cat6A and patch panels", "Hi team,",
             "We're fitting out Hall B and need your best pricing on the items below. Lead time matters, so please include availability.",
             ["Cat6A cable 305m box x 20", "30 x 24 port patch panel", "15 boxes cat 6 cable"],
             "Ship to 615 N 48th St, Phoenix, AZ 85008 (loading dock, Hall B).", "Best,",
             ["Sofia Rossi", "Infrastructure Lead | Sunbelt Colocation"], delivery="We need delivery by November 1."),
    _request("jcarter@peachtreepm.com", "James Carter", "Tools for our maintenance crew", "Good morning,",
             "Our maintenance crew needs a tool refresh. Could you quote:",
             ["8 pcs cordless drill 18V", "8 sets drill bit set", "16 x hard hats (white)"],
             "Ship to 1100 Spring St NW, Suite 300, Atlanta, GA 30309.", "Thank you,",
             ["James Carter", "Operations Manager", "Peachtree Property Management"]),
    _request("enguyen@harborviewhealth.com", "Emily Nguyen", "Quarterly glove order – Q4", "Hi,",
             "Please send a quotation for our Q4 glove order. We go through a lot of these, so volume pricing would be appreciated.",
             ["200 boxes medium nitrile gloves", "150 boxes large nitrile gloves"],
             "Ship to 150 Northern Ave, Boston, MA 02210 (central stores).", "Many thanks,",
             ["Emily Nguyen", "Materials Manager, Harborview Health Facilities"], delivery="We need delivery by October 5."),
    _request("carlos@desertsungc.com", "Carlos Mendoza", "Site kit for new crew", "Hey team,",
             "We have a new crew starting on the Russell Road job. Can you price a site kit?",
             ["12 x hard hats (white)", "12 x hi vis vest large", "12 x hi vis vest XL", "2 pcs cordless drill 18V"],
             "Ship to 3960 W Russell Rd, Las Vegas, NV 89118.", "Thanks,",
             ["Carlos Mendoza", "Superintendent | Desert Sun General Contracting"], bullet="•"),
    _request("hschmidt@northstarprecision.com", "Hannah Schmidt", "RFQ: fasteners and gloves for Line 3", "Hello,",
             "Please quote the following for our Line 3 assembly area:",
             ["50 boxes M8 bolts", "50 boxes M8 hex nuts", "50 boxes M8 washers", "20 boxes nitrile gloves size M"],
             "Ship to 7600 Boone Ave N, Brooklyn Park, MN 55428.", "Regards,",
             ["Hannah Schmidt", "Purchasing Specialist", "North Star Precision Manufacturing"],
             delivery="We need delivery by October 20.", bullet="1."),
    _request("rwilliams@trianglenetworkservices.com", "Robert Williams", "Client office network upgrade", "Hi,",
             "We're upgrading a client's office network next week. Could you quote:",
             ["Cat6 cable 305m box x 4", "3 x 24 port patch panel"],
             "Ship to 5000 Falls of Neuse Rd, Suite 108, Raleigh, NC 27609.", "Thanks!",
             ["Robert Williams", "Project Engineer, Triangle Network Services"]),
    _request("obrown@buckeyeindustrialdist.com", "Olivia Brown", "Stock replenishment – fasteners", "Hi team,",
             "Time for our monthly restock. Please send your best price on:",
             ["FS-HN-M8 x 500", "FS-WA-M8 x 500", "FS-HB-M8-50 x 250"],
             "Ship to 2250 Westbelt Dr, Columbus, OH 43228.", "Thank you,",
             ["Olivia Brown", "Senior Buyer | Buckeye Industrial Distributors"]),
    _request("daniel@goldengaterenovation.com", "Daniel Kim", "Quote for Mission District renovation", "Hi there,",
             "Can I get a quote for these for a renovation job we're starting in the Mission?",
             ["10 x hard hats (white)", "3 pcs cordless drill 18V", "15 x safety vests"],
             "Ship to 1600 Bryant St, San Francisco, CA 94103.", "Thanks,",
             ["Daniel Kim", "Owner, Golden Gate Renovation Co."]),
    _request("fali@gulfcoastmarineservices.com", "Fatima Ali", "Safety gear – dock team", "Hello,",
             "Please quote safety gear for our dock team at the port terminal:",
             ["40 x hi vis vest XL", "40 x hard hats (white)", "40 boxes nitrile gloves size L"],
             "Ship to 1901 Guy N Verger Blvd, Tampa, FL 33605.", "Kind regards,",
             ["Fatima Ali", "HSE Manager", "Gulf Coast Marine Services"], delivery="We need delivery by October 8.", bullet="•"),
    _request("tanderson@lonestarcabling.com", "Thomas Anderson", "Cable pricing – Energy Corridor install", "Hi,",
             "Need current pricing on cable for an upcoming install in the Energy Corridor:",
             ["Cat6 cable 305m box x 25", "Cat6A cable 305m box x 6"],
             "Ship to 7155 Old Katy Rd, Suite 250, Houston, TX 77024.", "Thanks,",
             ["Thomas Anderson", "Operations Director, Lone Star Structured Cabling"]),
    _request("grace.park@empireworkplace.com", "Grace Park", "Small order for our studio", "Hi,",
             "Just a small order this time. Could you quote:",
             ["2 sets drill bit set", "1 pcs cordless drill 18V"],
             "Ship to 236 W 30th St, Floor 5, New York, NY 10001.", "Best,",
             ["Grace Park", "Office Manager | Empire Workplace Interiors"]),
    _request("ahassan@cascadeutilityservices.com", "Ahmed Hassan", "PPE and tools for field technicians", "Good afternoon,",
             "Please quote PPE and tools for our field technicians ahead of storm season:",
             ["60 x hard hats (white)", "60 x hi vis vest large", "6 pcs cordless drill 18V"],
             "Ship to 5200 NE Columbia Blvd, Portland, OR 97218.", "Thank you,",
             ["Ahmed Hassan", "Fleet & Equipment Supervisor", "Cascade Utility Services"],
             delivery="We need delivery by October 15.", bullet="1."),
    _request("laura.martinez@redwoodsolarinc.com", "Laura Martinez", "Drills for two new install crews", "Hi team,",
             "We're adding two install crews and need to equip them. Please quote:",
             ["10 pcs cordless drill 18V", "10 sets drill bit set", "20 boxes M8 bolts"],
             "Ship to 2700 Alvarado St, Suite B, San Leandro, CA 94577.", "Thanks,",
             ["Laura Martinez", "Procurement Lead, Redwood Solar Installations"]),
    _request("kpatel@metrofleetmaintenance.com", "Kevin Patel", "Depot maintenance – quote needed", "Hello,",
             "Please quote the following for our Racine Avenue depot. Catalog numbers included where we had them.",
             ["TL-DR-18V x 15", "TL-BIT-HSS-19 x 15", "30 boxes nitrile gloves size L"],
             "Ship to 4300 S Racine Ave, Chicago, IL 60609.", "Regards,",
             ["Kevin Patel", "Depot Maintenance Manager", "Metro Fleet Maintenance"], delivery="We need delivery by October 12."),
    _request("rachel@silverlineevents.com", "Rachel Cohen", "Cabling for festival setup", "Hi!",
             "We've got a festival setup coming up and need a few things:",
             ["Cat6 cable 305m box x 2", "2 x 24 port patch panel", "10 x safety vests"],
             "Ship to 6435 S Valley View Blvd, Las Vegas, NV 89118.", "Thanks so much,",
             ["Rachel Cohen", "Production Coordinator | Silverline Event Production"],
             delivery="We need delivery by September 28.", bullet="•"),
    _request("sokafor@brightwaterusd.org", "Samuel Okafor", "Facilities supplies request", "Good morning,",
             "Please send a quote for our district facilities team:",
             ["20 boxes nitrile gloves size M", "10 x hard hats (white)", "5 sets drill bit set"],
             "Ship to 1250 Brightwater Pkwy, Marietta, GA 30067.", "Thank you,",
             ["Samuel Okafor", "Facilities Director", "Brightwater Unified School District"]),
]


def demo_request_body(request: dict) -> str:
    if request["bullet"] == "1.":
        items = [f"{number}. {item}" for number, item in enumerate(request["items"], start=1)]
    else:
        items = [f"{request['bullet']} {item}" for item in request["items"]]
    lines = [request["greeting"], "", request["intro"], "", *items, ""]
    if request["delivery"]:
        lines.append(request["delivery"])
    lines += [request["ship_to"], "", request["closing"], *request["signature"]]
    return "\n".join(lines)


def seed_if_empty(session: Session) -> bool:
    """Starter product catalogue, for a brand-new database."""
    if session.scalar(select(func.count()).select_from(Product)):
        return False
    for sku, name, aliases, unit, price, weight, tiers, (on_hand, in_days, back_days) in PRODUCTS:
        session.add(
            Product(
                sku=sku,
                name=name,
                aliases=aliases,
                unit=unit,
                base_price=D(price),
                weight_kg=D(weight),
                active=True,
                price_tiers=[PriceTier(min_qty=q, unit_price=D(p)) for q, p in tiers],
                inventory=InventoryItem(on_hand=on_hand, lead_time_days_in_stock=in_days, lead_time_days_backorder=back_days),
            )
        )
    session.commit()
    return True


def seed_demo_extras(session: Session) -> dict[str, int]:
    """Add sample tax rates, customers and contract prices that are missing. Existing rows are never changed,
    and a sample contract price someone deleted is not added back."""
    added = {"tax_rates": 0, "customers": 0, "customer_prices": 0}

    regions = set(session.scalars(select(TaxRate.region)))
    for region, label, rate in TAX_RATES:
        if region not in regions:
            session.add(TaxRate(region=region, label=label, rate=D(rate)))
            added["tax_rates"] += 1

    emails = set(session.scalars(select(Customer.email)))
    new_emails = set()
    for data in CUSTOMERS:
        if data["email"] not in emails:
            session.add(
                Customer(
                    **{**data, "discount_pct": D(data["discount_pct"])},
                    email_domain=data["email"].rpartition("@")[2],
                    is_verified=True,
                    is_sample=True,
                )
            )
            new_emails.add(data["email"])
    added["customers"] = len(new_emails)
    session.flush()

    prices_table_was_empty = not session.scalar(select(func.count()).select_from(CustomerPrice))
    customers = {c.email: c for c in session.scalars(select(Customer).where(Customer.email.in_({row[0] for row in CUSTOMER_PRICES})))}
    products = {p.sku: p for p in session.scalars(select(Product).where(Product.sku.in_({row[1] for row in CUSTOMER_PRICES})))}
    existing = set(session.execute(select(CustomerPrice.customer_id, CustomerPrice.product_id, CustomerPrice.min_qty)).tuples())
    for email, sku, min_qty, price, note in CUSTOMER_PRICES:
        customer, product = customers.get(email), products.get(sku)
        if customer is None or product is None or (customer.id, product.id, min_qty) in existing:
            continue
        if prices_table_was_empty or email in new_emails:
            session.add(CustomerPrice(customer_id=customer.id, product_id=product.id, min_qty=min_qty, unit_price=D(price), note=note))
            added["customer_prices"] += 1
    session.commit()
    return added


def _message_id(index: int, email: str) -> str:
    local, _, domain = email.partition("@")
    return f"<rfq-{index:02d}.{local}@{domain}>"


def _received_at(index: int, total: int, now: datetime) -> datetime:
    """Spread the sample requests over the last two weeks of business days, oldest first."""
    day = now.date() - timedelta(days=round((total - index) * 0.7))
    while day.weekday() >= 5:  # no weekend emails
        day -= timedelta(days=1)
    moment = datetime.combine(day, time(8 + (index * 5) % 9, (index * 17) % 60), tzinfo=timezone.utc)
    return min(moment, now - timedelta(minutes=45 * (total - index + 1)))


def seed_demo_quotes(service: QuoteService) -> list[Quote]:
    """Run each sample request email through the normal pipeline, dated over the last two weeks.
    Safe to repeat: quotes that already exist are returned unchanged."""
    now = utcnow()
    quotes = []
    for index, request in enumerate(DEMO_REQUESTS, start=1):
        message_id = _message_id(index, request["email"])
        is_new = service.session.scalar(select(Quote.id).where(Quote.source_message_id == message_id)) is None
        quote = service.ingest_email(
            InboundEmail(
                message_id=message_id,
                from_email=request["email"],
                from_name=request["name"],
                subject=request["subject"],
                body_text=demo_request_body(request),
            )
        )
        if is_new:
            received = _received_at(index, len(DEMO_REQUESTS), now)
            quote.created_at = received
            quote.valid_until = received.date() + timedelta(days=service.settings.quote_validity_days)
            for offset, event in enumerate(quote.events):
                event.at = received + timedelta(seconds=2 + offset * 3)
            service.refresh_pdf(quote)  # the PDF shows the quote date
            service.session.commit()
        quotes.append(quote)
    return quotes
