"""
technical.py
============

The layer that answers "yes, but what *is* it, chemically?"

ingredients_db.json and knowledge.py both write for a shopper standing in
an aisle: short, plain, no jargon. That's the right default, but it leaves
nowhere to put the specific compound, the INS/E code it's regulated under,
the functional class it belongs to, or what it's actually extracted from.

This module adds that second layer without touching the plain one. Every
ingredient card gains a `technical` block:

    {
      "chemical": "Lecithin — a mixture of phospholipids, mainly phosphatidylcholine",
      "ins":      "INS 322(i)",
      "family":   "Emulsifier (phospholipid)",
      "source":   "Degummed soybean or sunflower oil",
      "detail":   "...2-3 sentences of real chemistry...",
      "notes":    "...regulatory / sensitivity note, when there is one..."
    }

Lookup order per ingredient, first hit wins:

    1. INS / E number stated on the label  → INS_TECHNICAL
    2. vitamin or mineral name             → MICRONUTRIENTS
    3. ingredient-type pattern             → TECH_RULES
    4. category-level fallback             → CATEGORY_FALLBACK

Second job: pulling the vitamins and minerals out of the label. Labels
declare fortification in several shapes — "VITAMINS (B1, B2, B3, B6, D)",
"VITAMINS & MINERALS", a bare "VITAMINS", or only in the nutrition panel
as "Vitamin D 30% RDA". extract_micronutrients() catches all of those and
returns a card per nutrient with its chemical form, its role in the body,
what deficiency looks like, and a reference intake.

Reference intakes here are rounded adult ranges, not a prescription. They
exist so a "% RDA" printed on a pack means something to the reader.
"""

import re

# ============================================================
# INS / E NUMBERS
# ============================================================
# Keyed by the bare code. Sub-forms ("322(i)", "150d", "472e") are looked
# up whole first, then fall back to the parent number.
#
# Fields: chemical, family, source, detail, notes

INS_TECHNICAL = {
    # --- colours ---
    "100": {
        "chemical": "Curcumin (diferuloylmethane)",
        "family": "Colour (natural, polyphenol)",
        "source": "Rhizome of Curcuma longa — turmeric",
        "detail": "The yellow pigment fraction of turmeric, extracted with solvent and purified. Fat-soluble, sensitive to light, and shifts reddish-brown above pH 7.",
        "notes": "",
    },
    "101": {
        "chemical": "Riboflavin (vitamin B2)",
        "family": "Colour (natural) — also a vitamin",
        "source": "Fermentation, or synthesised",
        "detail": "The same molecule as dietary vitamin B2, used here for its yellow-orange colour. It degrades quickly in light, which is why riboflavin-coloured products are often opaque-packed.",
        "notes": "",
    },
    "102": {
        "chemical": "Tartrazine (trisodium salt of a pyrazolone azo dye)",
        "family": "Colour (synthetic azo dye)",
        "source": "Petrochemical synthesis",
        "detail": "A water-soluble lemon-yellow azo dye, very stable to heat, light and acid — which is why it survives baking and long shelf lives where a natural pigment wouldn't.",
        "notes": "Carries a warning label in the EU about possible effects on activity and attention in children. Intolerance is reported in a small number of people.",
    },
    "110": {
        "chemical": "Sunset Yellow FCF",
        "family": "Colour (synthetic azo dye)",
        "source": "Petrochemical synthesis",
        "detail": "An orange-yellow sulphonated azo dye, water-soluble and stable across the pH range used in soft drinks and confectionery.",
        "notes": "Also carries the EU activity-and-attention advisory label.",
    },
    "120": {
        "chemical": "Carmines / carminic acid",
        "family": "Colour (natural, animal-derived)",
        "source": "Dried bodies of the cochineal insect",
        "detail": "An anthraquinone pigment extracted from cochineal and precipitated onto an aluminium or calcium substrate to make a stable red lake.",
        "notes": "Insect-derived, so not vegetarian or vegan. A recognised, if uncommon, allergen.",
    },
    "122": {
        "chemical": "Azorubine / Carmoisine",
        "family": "Colour (synthetic azo dye)",
        "source": "Petrochemical synthesis",
        "detail": "A red azo dye used where a strong, heat-stable red is needed — jellies, desserts, sauces.",
        "notes": "Also carries the EU activity-and-attention advisory label.",
    },
    "124": {
        "chemical": "Ponceau 4R (cochineal red A)",
        "family": "Colour (synthetic azo dye)",
        "source": "Petrochemical synthesis",
        "detail": "A strawberry-red azo dye, despite the name entirely synthetic and unrelated to cochineal insects.",
        "notes": "Also carries the EU activity-and-attention advisory label.",
    },
    "129": {
        "chemical": "Allura Red AC",
        "family": "Colour (synthetic azo dye)",
        "source": "Petrochemical synthesis",
        "detail": "A dark-red azo dye, the usual replacement for amaranth in beverages and confectionery.",
        "notes": "Also carries the EU activity-and-attention advisory label.",
    },
    "133": {
        "chemical": "Brilliant Blue FCF",
        "family": "Colour (synthetic triarylmethane dye)",
        "source": "Petrochemical synthesis",
        "detail": "A blue triarylmethane dye, very poorly absorbed in the gut. Blended with tartrazine it produces the greens used in ice creams and icings.",
        "notes": "",
    },
    "150": {
        "chemical": "Caramel colour",
        "family": "Colour (natural origin, processed)",
        "source": "Controlled heating of carbohydrates, usually glucose syrup",
        "detail": "Made by heating sugars with an acid, alkali or salt catalyst. Four classes exist (150a–150d), differing by catalyst and in the charge the resulting colloid carries, which decides what it can be used in without curdling.",
        "notes": "",
    },
    "150a": {
        "chemical": "Plain (caustic) caramel",
        "family": "Colour (natural origin, processed)",
        "source": "Heated carbohydrate, no ammonium or sulphite catalyst",
        "detail": "Class I caramel — sugars heated with only acid or alkali. The mildest of the four classes and the one used in spirits and baked goods.",
        "notes": "",
    },
    "150c": {
        "chemical": "Ammonia caramel",
        "family": "Colour (natural origin, processed)",
        "source": "Carbohydrate heated with ammonium compounds",
        "detail": "Class III caramel, carrying a positive colloidal charge. Standard in beers, sauces and gravies.",
        "notes": "",
    },
    "150d": {
        "chemical": "Sulphite ammonia caramel",
        "family": "Colour (natural origin, processed)",
        "source": "Carbohydrate heated with both sulphite and ammonium compounds",
        "detail": "Class IV caramel, negatively charged so it stays dispersed in acidic drinks. This is the colour in most colas.",
        "notes": "Manufacturing can form 4-methylimidazole, which is limited by regulation in several countries.",
    },
    "160": {
        "chemical": "Carotenoid pigments",
        "family": "Colour (natural, carotenoid)",
        "source": "Plant extract, algae, or synthesis",
        "detail": "Fat-soluble yellow-to-orange pigments. Some, like beta-carotene, are also precursors the body converts into vitamin A.",
        "notes": "",
    },
    "160a": {
        "chemical": "Carotenes (alpha-, beta-, gamma-carotene)",
        "family": "Colour (natural, carotenoid) — provitamin A",
        "source": "Carrot or algal extract, or synthesised",
        "detail": "A fat-soluble orange pigment which the body can cleave into retinol, so it doubles as a vitamin A source as well as a colour.",
        "notes": "",
    },
    "160b": {
        "chemical": "Annatto (bixin and norbixin)",
        "family": "Colour (natural, carotenoid)",
        "source": "Seed coat of Bixa orellana",
        "detail": "Bixin is the oil-soluble form, norbixin the water-soluble salt. It is the traditional colour of cheddar, butter and many snack coatings.",
        "notes": "Occasional reports of sensitivity.",
    },
    "163": {
        "chemical": "Anthocyanins",
        "family": "Colour (natural, flavonoid)",
        "source": "Grape skin, black carrot, hibiscus, berries",
        "detail": "Water-soluble pigments that change colour with pH — red in acid, purple-to-blue as it rises — so they are used mostly in acidic products where the shade stays put.",
        "notes": "",
    },
    "171": {
        "chemical": "Titanium dioxide",
        "family": "Colour (inorganic pigment, opacifier)",
        "source": "Refined titanium ore",
        "detail": "An insoluble white pigment used to make coatings opaque rather than to add a hue.",
        "notes": "No longer permitted as a food additive in the EU following a 2021 EFSA reassessment. Still permitted in several other jurisdictions.",
    },
    # --- preservatives ---
    "200": {
        "chemical": "Sorbic acid",
        "family": "Preservative (mould and yeast inhibitor)",
        "source": "Synthesised",
        "detail": "A short-chain unsaturated fatty acid that crosses microbial cell membranes in its undissociated form, so it only works well in acidic products — roughly below pH 6.5.",
        "notes": "",
    },
    "202": {
        "chemical": "Potassium sorbate",
        "family": "Preservative (mould and yeast inhibitor)",
        "source": "Synthesised — potassium salt of sorbic acid",
        "detail": "The water-soluble salt of sorbic acid, added in preference to the acid itself because it dissolves far more easily. It converts back to active sorbic acid in the acidic food.",
        "notes": "Among the better-tolerated preservatives, with a high acceptable daily intake.",
    },
    "210": {
        "chemical": "Benzoic acid",
        "family": "Preservative (yeast and bacteria inhibitor)",
        "source": "Synthesised; also occurs naturally in cranberries and cinnamon",
        "detail": "Active only in its undissociated form, so it needs an acidic product — typically below pH 4.5 — to do anything useful.",
        "notes": "",
    },
    "211": {
        "chemical": "Sodium benzoate",
        "family": "Preservative (yeast and bacteria inhibitor)",
        "source": "Synthesised — sodium salt of benzoic acid",
        "detail": "The soluble salt of benzoic acid, standard in acidic drinks, pickles and sauces.",
        "notes": "In the presence of ascorbic acid and warmth it can form trace benzene, which is why the two are usually formulated apart.",
    },
    "220": {
        "chemical": "Sulphur dioxide",
        "family": "Preservative and antioxidant",
        "source": "Gas, generated or applied directly",
        "detail": "Inhibits microbes and also blocks enzymatic browning by reacting with the carbonyl groups that drive it.",
        "notes": "Declarable allergen above 10 mg/kg. Can trigger reactions in people with asthma.",
    },
    "223": {
        "chemical": "Sodium metabisulphite",
        "family": "Preservative and antioxidant (sulphite)",
        "source": "Synthesised",
        "detail": "A solid that releases sulphur dioxide when dissolved, making it the convenient way to deliver SO2 into dried fruit, juices and some flours.",
        "notes": "Declarable allergen above 10 mg/kg. Can trigger reactions in people with asthma.",
    },
    "250": {
        "chemical": "Sodium nitrite",
        "family": "Preservative (curing salt)",
        "source": "Synthesised",
        "detail": "Used in cured meat to block Clostridium botulinum and to fix the pink colour by binding myoglobin as nitrosomyoglobin.",
        "notes": "Can form nitrosamines at high cooking temperatures. Use levels are tightly capped by regulation.",
    },
    "280": {
        "chemical": "Propionic acid",
        "family": "Preservative (mould inhibitor)",
        "source": "Synthesised; also a product of dairy fermentation",
        "detail": "The standard anti-mould agent in bread, chosen because it doesn't interfere with yeast the way sorbates and benzoates do.",
        "notes": "",
    },
    "282": {
        "chemical": "Calcium propionate",
        "family": "Preservative (mould inhibitor)",
        "source": "Synthesised — calcium salt of propionic acid",
        "detail": "Extends the mould-free life of bread without slowing the yeast that has to raise it.",
        "notes": "",
    },
    # --- antioxidants and acids ---
    "260": {
        "chemical": "Acetic acid",
        "family": "Acidity regulator / preservative",
        "source": "Fermentation or synthesis",
        "detail": "Vinegar's active acid. Lowers pH enough to slow microbial growth and adds a sharp note of its own.",
        "notes": "",
    },
    "270": {
        "chemical": "Lactic acid",
        "family": "Acidity regulator",
        "source": "Bacterial fermentation of carbohydrate",
        "detail": "A mild organic acid, softer in taste than citric, used where tartness has to be adjusted without a fruity edge.",
        "notes": "",
    },
    "296": {
        "chemical": "Malic acid",
        "family": "Acidity regulator",
        "source": "Synthesised; naturally the main acid in apples",
        "detail": "Holds tartness longer on the palate than citric acid, which is why it shows up in sour confectionery.",
        "notes": "",
    },
    "300": {
        "chemical": "Ascorbic acid (vitamin C)",
        "family": "Antioxidant — also a vitamin",
        "source": "Fermentation-based synthesis from glucose",
        "detail": "Scavenges oxygen and reduces oxidised compounds back to their original state, protecting colour and flavour. In bread dough it does the opposite job, strengthening gluten as a flour treatment agent.",
        "notes": "",
    },
    "306": {
        "chemical": "Mixed tocopherols concentrate",
        "family": "Antioxidant (natural, vitamin E family)",
        "source": "Vegetable oil distillates, usually soy or sunflower",
        "detail": "Chain-breaking antioxidants that donate a hydrogen atom to the free radicals produced as fat oxidises, stopping the chain reaction that produces rancid flavours.",
        "notes": "",
    },
    "307": {
        "chemical": "Alpha-tocopherol",
        "family": "Antioxidant (vitamin E family)",
        "source": "Synthesised or isolated from vegetable oil",
        "detail": "The single most biologically active tocopherol, used both as a fat-phase antioxidant and as a vitamin E source.",
        "notes": "",
    },
    "307b": {
        "chemical": "Tocopherol concentrate, mixed",
        "family": "Antioxidant (natural, vitamin E family)",
        "source": "Distillate of vegetable oils — commonly soy, sunflower or rapeseed",
        "detail": "A blend of alpha-, beta-, gamma- and delta-tocopherols recovered from oil refining. It sits in the fat phase and interrupts lipid peroxidation, which is what keeps biscuits and fried snacks from tasting cardboard-stale.",
        "notes": "Considered one of the gentler antioxidant options — it is chemically the same family as dietary vitamin E.",
    },
    "310": {
        "chemical": "Propyl gallate",
        "family": "Antioxidant (synthetic phenolic)",
        "source": "Synthesised — propyl ester of gallic acid",
        "detail": "Protects fats from oxidation, often paired with BHA or citric acid because the combination works better than either alone.",
        "notes": "",
    },
    "319": {
        "chemical": "Tertiary butylhydroquinone (TBHQ)",
        "family": "Antioxidant (synthetic phenolic)",
        "source": "Petrochemical synthesis",
        "detail": "The most heat-stable of the common synthetic antioxidants, which is why it survives deep frying and ends up in instant noodles and fried snacks.",
        "notes": "Permitted levels are capped tightly — typically 200 mg/kg of fat. Not permitted in some jurisdictions.",
    },
    "320": {
        "chemical": "Butylated hydroxyanisole (BHA)",
        "family": "Antioxidant (synthetic phenolic)",
        "source": "Petrochemical synthesis",
        "detail": "A fat-soluble antioxidant that carries over from frying oil into the fried product, giving downstream protection.",
        "notes": "Classified by IARC as possibly carcinogenic to humans based on rodent forestomach data of contested relevance. Use levels are capped.",
    },
    "321": {
        "chemical": "Butylated hydroxytoluene (BHT)",
        "family": "Antioxidant (synthetic phenolic)",
        "source": "Petrochemical synthesis",
        "detail": "Used in fats and in packaging material itself, where it migrates slowly into the food to keep protecting it.",
        "notes": "Permitted levels are capped by regulation.",
    },
    "322": {
        "chemical": "Lecithin — mainly phosphatidylcholine, with phosphatidylethanolamine and inositol phospholipids",
        "family": "Emulsifier (phospholipid)",
        "source": "Degummed soybean or sunflower oil, occasionally egg yolk",
        "detail": "Each molecule has a water-loving phosphate head and two fat-loving fatty acid tails, so it sits at the oil-water boundary and holds the two phases together. In chocolate it does something subtler: it coats sugar crystals and cuts the viscosity of the melt, so less cocoa butter is needed.",
        "notes": "Soy lecithin is derived from a declarable allergen, though the refining removes nearly all soy protein.",
    },
    "325": {
        "chemical": "Sodium lactate",
        "family": "Acidity regulator / humectant",
        "source": "Neutralised fermentation lactic acid",
        "detail": "Holds moisture and buffers pH; in meat products it also slows bacterial growth.",
        "notes": "",
    },
    "330": {
        "chemical": "Citric acid",
        "family": "Acidity regulator / antioxidant synergist",
        "source": "Fermentation of carbohydrate by Aspergillus niger",
        "detail": "The workhorse food acid. Besides adding tartness it chelates trace metal ions that would otherwise catalyse oxidation, which is why it turns up alongside antioxidants in fatty foods.",
        "notes": "Despite the name, commercial citric acid is a fermentation product, not citrus-derived.",
    },
    "331": {
        "chemical": "Sodium citrates",
        "family": "Acidity regulator / sequestrant",
        "source": "Neutralised citric acid",
        "detail": "Buffers pH and binds calcium ions — the property that lets processed cheese melt smoothly instead of splitting into oil and curds.",
        "notes": "",
    },
    "334": {
        "chemical": "Tartaric acid",
        "family": "Acidity regulator",
        "source": "By-product of winemaking",
        "detail": "A sharp fruit acid; its potassium salt is cream of tartar, half of classic baking powder.",
        "notes": "",
    },
    "338": {
        "chemical": "Phosphoric acid",
        "family": "Acidity regulator",
        "source": "Synthesised from phosphate rock",
        "detail": "A strong mineral acid that gives colas their bite — notably sharper and flatter than the fruit acids.",
        "notes": "High intakes of phosphate additives are discussed in the context of bone and kidney health.",
    },
    "339": {
        "chemical": "Sodium phosphates",
        "family": "Acidity regulator / emulsifying salt / raising acid",
        "source": "Synthesised",
        "detail": "A family of mono-, di- and tribasic salts. They buffer pH, hold water in meat and dairy, and act as the acid half of some chemical raising systems.",
        "notes": "",
    },
    "341": {
        "chemical": "Calcium phosphates",
        "family": "Raising acid / anticaking agent / firming agent",
        "source": "Synthesised",
        "detail": "Monocalcium phosphate is the fast-acting acid in double-acting baking powder; the di- and tri-basic forms are used to stop caking and to fortify with calcium.",
        "notes": "",
    },
    # --- gums, stabilisers, thickeners ---
    "406": {
        "chemical": "Agar (galactan polysaccharide)",
        "family": "Gelling agent",
        "source": "Red seaweed, Gelidium and Gracilaria species",
        "detail": "Forms a firm, brittle, heat-reversible gel that sets around 35 °C but doesn't melt again until near 85 °C — an unusually wide hysteresis gap.",
        "notes": "",
    },
    "407": {
        "chemical": "Carrageenan (sulphated galactans — kappa, iota, lambda)",
        "family": "Stabiliser / gelling agent",
        "source": "Red seaweed, chiefly Kappaphycus and Eucheuma",
        "detail": "The sulphate groups bind milk casein, which is why a very small amount keeps cocoa particles suspended in chocolate milk. Kappa gels firmly, iota softly, lambda not at all.",
        "notes": "Degraded carrageenan (poligeenan) is a different, non-food material — the two are often confused in online discussion.",
    },
    "410": {
        "chemical": "Locust bean gum (galactomannan)",
        "family": "Thickener / stabiliser",
        "source": "Endosperm of carob seeds",
        "detail": "A galactomannan with sparse side-chains, which lets the backbones associate with xanthan or kappa-carrageenan to form gels neither makes alone.",
        "notes": "",
    },
    "412": {
        "chemical": "Guar gum (galactomannan)",
        "family": "Thickener / stabiliser",
        "source": "Endosperm of the guar bean, Cyamopsis tetragonoloba",
        "detail": "Hydrates in cold water and thickens at very low concentrations. In frozen desserts it limits ice crystal growth, which is what keeps the texture smooth through freeze-thaw cycles.",
        "notes": "A soluble fibre — large amounts can be laxative, but label-level doses are far below that.",
    },
    "414": {
        "chemical": "Gum arabic (arabinogalactan protein complex)",
        "family": "Stabiliser / emulsifier / glazing agent",
        "source": "Hardened sap of Acacia senegal and Acacia seyal",
        "detail": "Unusual among gums in having a protein fraction, which lets it emulsify as well as thicken — the reason it stabilises citrus oils in soft drinks.",
        "notes": "Counts as dietary fibre.",
    },
    "415": {
        "chemical": "Xanthan gum (heteropolysaccharide)",
        "family": "Thickener / stabiliser",
        "source": "Fermentation of glucose by Xanthomonas campestris",
        "detail": "Strongly shear-thinning: thick at rest, thin while poured or stirred, thick again once still. That profile is why it suspends spice particles in a dressing that still pours.",
        "notes": "",
    },
    "440": {
        "chemical": "Pectin (polygalacturonic acid, variably methyl-esterified)",
        "family": "Gelling agent",
        "source": "Citrus peel and apple pomace",
        "detail": "High-methoxyl pectin needs sugar and acid to gel — the classic jam system. Low-methoxyl pectin gels with calcium instead, which is how reduced-sugar jams set.",
        "notes": "A soluble fibre.",
    },
    "450": {
        "chemical": "Diphosphates (pyrophosphates)",
        "family": "Raising acid / emulsifying salt / sequestrant",
        "source": "Synthesised",
        "detail": "The slow-acting acid component of most baking powders: it reacts with bicarbonate mainly in oven heat rather than in the bowl, so the rise happens where it's wanted.",
        "notes": "",
    },
    "460": {
        "chemical": "Cellulose",
        "family": "Bulking agent / anticaking agent",
        "source": "Purified plant fibre, usually wood pulp or cotton linters",
        "detail": "An indigestible glucose polymer used to add bulk and fibre and to keep shredded products from clumping.",
        "notes": "Passes through undigested — it contributes fibre, not calories.",
    },
    "466": {
        "chemical": "Sodium carboxymethyl cellulose (CMC)",
        "family": "Thickener / stabiliser",
        "source": "Chemically modified plant cellulose",
        "detail": "Cellulose with carboxymethyl groups attached, which makes the otherwise insoluble polymer dissolve and thicken in cold water.",
        "notes": "",
    },
    "471": {
        "chemical": "Mono- and diglycerides of fatty acids",
        "family": "Emulsifier",
        "source": "Glycerolysis of vegetable or animal fats",
        "detail": "Ordinary triglycerides with one or two fatty acids removed, leaving free hydroxyl groups that give the molecule a water-liking end. In bread they complex with starch and slow the staling that firms up a loaf.",
        "notes": "The fat source is not always stated, so the ingredient may be animal-derived unless the pack says otherwise.",
    },
    "472": {
        "chemical": "Organic acid esters of mono- and diglycerides",
        "family": "Emulsifier (dough strengthener / aerating agent)",
        "source": "Mono- and diglycerides esterified with an organic acid",
        "detail": "The suffix letter names the acid — acetic (a), lactic (b), citric (c), tartaric (d), diacetyl tartaric (e). Each shifts the balance between water- and fat-liking, tuning the emulsifier to a specific job.",
        "notes": "",
    },
    "472e": {
        "chemical": "Diacetyl tartaric acid esters of mono- and diglycerides (DATEM)",
        "family": "Emulsifier (dough strengthener)",
        "source": "Mono- and diglycerides esterified with diacetyl tartaric acid",
        "detail": "Interacts with gluten to make dough hold gas better, giving a higher rise and a finer, more even crumb in industrial bread.",
        "notes": "",
    },
    "476": {
        "chemical": "Polyglycerol polyricinoleate (PGPR)",
        "family": "Emulsifier (viscosity modifier)",
        "source": "Castor oil fatty acids esterified with polyglycerol",
        "detail": "Used almost exclusively in chocolate, where it collapses the yield stress of the melt — the resistance that has to be overcome before the chocolate flows at all. That lets moulds fill cleanly with less cocoa butter.",
        "notes": "",
    },
    "481": {
        "chemical": "Sodium stearoyl-2-lactylate (SSL)",
        "family": "Emulsifier (dough strengthener)",
        "source": "Stearic acid and lactic acid, neutralised with sodium",
        "detail": "Binds to gluten proteins and to starch at once, strengthening dough and slowing staling in the same molecule.",
        "notes": "",
    },
    "491": {
        "chemical": "Sorbitan monostearate",
        "family": "Emulsifier",
        "source": "Sorbitol esterified with stearic acid",
        "detail": "A fat-loving emulsifier, typically paired with a water-loving polysorbate to hit an intermediate balance one alone can't reach.",
        "notes": "",
    },
    # --- raising agents, anticaking, misc ---
    "500": {
        "chemical": "Sodium carbonates",
        "family": "Raising agent / acidity regulator",
        "source": "Synthesised (Solvay process)",
        "detail": "The family covers sodium carbonate 500(i), sodium bicarbonate 500(ii) and the sesquicarbonate 500(iii). All release carbon dioxide on reaction with acid or on heating.",
        "notes": "",
    },
    "500ii": {
        "chemical": "Sodium bicarbonate (NaHCO3)",
        "family": "Raising agent",
        "source": "Synthesised",
        "detail": "Baking soda. It needs an acid partner — a phosphate, cream of tartar, or acidity in the batter itself — to release its carbon dioxide; without one, the leftover carbonate tastes soapy.",
        "notes": "Contributes sodium to the product, though usually a small share of the total.",
    },
    "503": {
        "chemical": "Ammonium carbonates",
        "family": "Raising agent",
        "source": "Synthesised",
        "detail": "Breaks down completely into ammonia, carbon dioxide and water, leaving no salt residue behind. That makes it the classic leavener for thin, dry biscuits — where the ammonia can escape — but useless in moist cakes, which would trap it.",
        "notes": "",
    },
    "508": {
        "chemical": "Potassium chloride",
        "family": "Salt substitute / firming agent",
        "source": "Mined or from brine",
        "detail": "Tastes salty via the same mechanism as sodium chloride but carries a metallic bitter note, so it is usually blended rather than used alone in reduced-sodium products.",
        "notes": "",
    },
    "451": {
        "chemical": "Triphosphates (sodium and potassium tripolyphosphate)",
        "family": "Humectant / sequestrant / emulsifying salt",
        "source": "Synthesised",
        "detail": "Binds water and metal ions. In noodles and meat it holds moisture through cooking, and in processed dairy it keeps proteins dispersed so the product melts rather than splits.",
        "notes": "Phosphate additives are absorbed more completely than the phosphorus naturally bound in whole foods.",
    },
    "452": {
        "chemical": "Polyphosphates",
        "family": "Humectant / sequestrant / stabiliser",
        "source": "Synthesised",
        "detail": "Longer phosphate chains than the di- and triphosphates, used to hold water and stop protein aggregation in processed products.",
        "notes": "Phosphate additives are absorbed more completely than naturally bound phosphorus.",
    },
    "501": {
        "chemical": "Potassium carbonates",
        "family": "Acidity regulator / raising agent",
        "source": "Synthesised",
        "detail": "Alkaline salts that raise pH. In noodles this is half of the traditional kansui alkali that gives ramen and instant noodles their springy bite and yellow colour, by changing how gluten and flour flavonoids behave.",
        "notes": "",
    },
    "551": {
        "chemical": "Silicon dioxide (amorphous silica)",
        "family": "Anticaking agent",
        "source": "Synthesised precipitated or fumed silica",
        "detail": "Extremely fine particles coat larger powder grains and adsorb surface moisture, so powders stay free-flowing instead of clumping.",
        "notes": "Inert and unabsorbed in the gut.",
    },
    "553": {
        "chemical": "Magnesium silicates / talc",
        "family": "Anticaking agent",
        "source": "Mined mineral, purified",
        "detail": "A plate-like mineral that stops particles sticking, used in powdered and coated products.",
        "notes": "",
    },
    "575": {
        "chemical": "Glucono delta-lactone (GDL)",
        "family": "Acidity regulator / raising acid",
        "source": "Oxidation of glucose",
        "detail": "Hydrolyses slowly in water to gluconic acid, giving a gradual, controllable drop in pH — the mechanism behind tofu coagulation and some slow-set dairy.",
        "notes": "",
    },
    "621": {
        "chemical": "Monosodium glutamate (MSG)",
        "family": "Flavour enhancer",
        "source": "Bacterial fermentation of starch or molasses",
        "detail": "The sodium salt of glutamic acid, an amino acid abundant in tomatoes, aged cheese and seaweed. Free glutamate binds umami taste receptors directly, which is what makes savoury notes read as fuller.",
        "notes": "Large controlled trials have not reproduced the symptom cluster once attributed to it, but some people report sensitivity.",
    },
    "627": {
        "chemical": "Disodium guanylate",
        "family": "Flavour enhancer (nucleotide)",
        "source": "Yeast extract or fermentation",
        "detail": "On its own it does very little. Paired with glutamate it multiplies the umami effect several times over, so the two are almost always declared together.",
        "notes": "A purine — relevant to people managing gout, though label quantities are small.",
    },
    "631": {
        "chemical": "Disodium inosinate",
        "family": "Flavour enhancer (nucleotide)",
        "source": "Fermentation, or from meat and fish tissue",
        "detail": "The same synergy as guanylate: near-inert alone, strongly umami-boosting alongside glutamate.",
        "notes": "A purine. Can be animal-derived unless the pack states otherwise.",
    },
    "635": {
        "chemical": "Disodium 5'-ribonucleotides (inosinate + guanylate)",
        "family": "Flavour enhancer (nucleotide blend)",
        "source": "Fermentation",
        "detail": "A ready-made 50:50 blend of the two nucleotide enhancers, sold as one ingredient because they are almost always used together.",
        "notes": "A purine.",
    },
    "900": {
        "chemical": "Polydimethylsiloxane",
        "family": "Anti-foaming agent",
        "source": "Synthesised silicone polymer",
        "detail": "Lowers surface tension so foam collapses — used in frying oils and some beverages at very low levels.",
        "notes": "",
    },
    "903": {
        "chemical": "Carnauba wax",
        "family": "Glazing agent",
        "source": "Leaves of the Brazilian palm Copernicia prunifera",
        "detail": "A hard, high-melting plant wax that gives confectionery and coated tablets their shine and a moisture barrier.",
        "notes": "",
    },
    "920": {
        "chemical": "L-cysteine",
        "family": "Flour treatment agent",
        "source": "Fermentation, or hydrolysis of keratin",
        "detail": "A reducing amino acid that breaks disulphide bonds in gluten, relaxing dough so it can be machined and proved faster.",
        "notes": "Historically sourced from hair or feathers; most food-grade material today is fermentation-derived.",
    },
    "950": {
        "chemical": "Acesulfame potassium (Ace-K)",
        "family": "Sweetener (high-intensity, non-nutritive)",
        "source": "Synthesised",
        "detail": "Around 200 times sweeter than sucrose, heat-stable, and excreted unchanged. Its slight bitter tail is usually masked by blending with aspartame or sucralose.",
        "notes": "",
    },
    "951": {
        "chemical": "Aspartame (aspartyl-phenylalanine methyl ester)",
        "family": "Sweetener (high-intensity, nutritive but negligible at use levels)",
        "source": "Synthesised from two amino acids",
        "detail": "About 200 times sweeter than sucrose. It is digested into aspartic acid, phenylalanine and methanol, all in quantities smaller than a normal meal provides. Not heat-stable, so it is rare in baked goods.",
        "notes": "Must carry a phenylalanine warning for people with phenylketonuria.",
    },
    "954": {
        "chemical": "Saccharin",
        "family": "Sweetener (high-intensity, non-nutritive)",
        "source": "Synthesised",
        "detail": "The oldest synthetic sweetener, 300–400 times sweeter than sucrose, with a metallic aftertaste usually blended away.",
        "notes": "The 1970s bladder-tumour findings in rats were later shown to run through a mechanism that does not apply to humans; it was delisted as a suspected carcinogen in 2000.",
    },
    "955": {
        "chemical": "Sucralose (trichlorogalactosucrose)",
        "family": "Sweetener (high-intensity, non-nutritive)",
        "source": "Synthesised from sucrose",
        "detail": "Sucrose with three hydroxyl groups swapped for chlorine, which makes it around 600 times sweeter and largely unabsorbable. Stable enough to survive baking.",
        "notes": "",
    },
    "960": {
        "chemical": "Steviol glycosides (rebaudioside A, stevioside)",
        "family": "Sweetener (high-intensity, plant-derived)",
        "source": "Leaves of Stevia rebaudiana",
        "detail": "200–350 times sweeter than sucrose. The individual glycosides differ in how clean they taste — Reb A and Reb M are preferred because they carry less of the liquorice-bitter tail.",
        "notes": "",
    },
    "965": {
        "chemical": "Maltitol",
        "family": "Sweetener (polyol / sugar alcohol)",
        "source": "Hydrogenated maltose from starch",
        "detail": "About 90% as sweet as sucrose with roughly half the calories, and it bulks like sugar — which is why it, not the high-intensity sweeteners, is used in sugar-free chocolate.",
        "notes": "Incompletely absorbed; larger amounts can cause bloating or a laxative effect.",
    },
    "967": {
        "chemical": "Xylitol",
        "family": "Sweetener (polyol / sugar alcohol)",
        "source": "Hydrogenation of xylose from birch or corn cobs",
        "detail": "As sweet as sucrose, with a strong cooling sensation as it dissolves. Oral bacteria cannot ferment it, which is why it appears in dental chewing gum.",
        "notes": "Incompletely absorbed; larger amounts can be laxative. Severely toxic to dogs.",
    },
    "1400": {
        "chemical": "Modified starch",
        "family": "Thickener / stabiliser",
        "source": "Maize, tapioca, potato or wheat starch, physically or chemically treated",
        "detail": "Native starch breaks down under heat, acid and stirring. Modification — cross-linking, acetylation, oxidation — rebuilds it to hold its viscosity through processing and freeze-thaw.",
        "notes": "'Modified' here means chemically or physically treated, not genetically modified.",
    },
    "1422": {
        "chemical": "Acetylated distarch adipate",
        "family": "Thickener / stabiliser (modified starch)",
        "source": "Maize, tapioca or waxy starch, cross-linked and acetylated",
        "detail": "Cross-linking keeps the granule from bursting under heat and shear; acetylation keeps the paste from retrograding in the fridge. Together they give a sauce that survives canning and chilling.",
        "notes": "",
    },
    "1442": {
        "chemical": "Hydroxypropyl distarch phosphate",
        "family": "Thickener / stabiliser (modified starch)",
        "source": "Cross-linked, hydroxypropylated starch",
        "detail": "Built for freeze-thaw stability — the hydroxypropyl groups stop starch chains re-associating and squeezing water out when the product thaws.",
        "notes": "",
    },
}


# ============================================================
# VITAMINS AND MINERALS
# ============================================================
# Keyed by a canonical id. `patterns` are matched against the label text.
#
# Reference intakes are rounded adult ranges drawn from commonly used
# national reference values. They are context for a "% RDA" on a pack,
# not a target to hit or a medical recommendation.

MICRONUTRIENTS = {
    "vitamin_a": {
        "name": "Vitamin A",
        "emoji": "🥕",
        "patterns": [r"vitamin\s*a\b", r"\bretinyl\b", r"\bretinol\b", r"\bbeta[\s-]?carotene\b"],
        "group": "Fat-soluble vitamin",
        "chemical": "Retinyl acetate or retinyl palmitate; sometimes beta-carotene as a precursor",
        "role": "Needed for the light-sensing pigment in the retina, for the integrity of skin and gut lining, and for immune cell function.",
        "deficiency": "Night blindness is the earliest classic sign; prolonged deficiency damages the cornea.",
        "reference": "Adult reference intake around 600–900 µg retinol equivalents a day.",
        "detail": "Fortification uses the ester forms because free retinol oxidises quickly. Being fat-soluble, it is stored in the liver rather than flushed out, so it is one of the few vitamins where sustained high supplemental intake actually matters.",
    },
    "vitamin_d": {
        "name": "Vitamin D",
        "emoji": "☀️",
        "patterns": [r"vitamin\s*d\s*[23]?\b", r"\bcholecalciferol\b", r"\bergocalciferol\b"],
        "group": "Fat-soluble vitamin",
        "chemical": "Cholecalciferol (D3, animal or lanolin-derived) or ergocalciferol (D2, from yeast)",
        "role": "Controls calcium and phosphate absorption from the gut, which is what allows bone to mineralise properly.",
        "deficiency": "Rickets in children, osteomalacia and bone pain in adults.",
        "reference": "Adult reference intake around 10–15 µg (400–600 IU) a day.",
        "detail": "Technically a prohormone rather than a vitamin: skin makes it from UVB exposure, and the liver and kidney then hydroxylate it twice to the active form. Fortification exists because sun exposure alone is unreliable — clothing, latitude, air quality and indoor work all cut it.",
    },
    "vitamin_e": {
        "name": "Vitamin E",
        "emoji": "🌻",
        "patterns": [r"vitamin\s*e\b", r"\btocopheryl\b", r"\balpha[\s-]?tocopherol\b"],
        "group": "Fat-soluble vitamin",
        "chemical": "DL-alpha-tocopheryl acetate is the usual fortification form",
        "role": "The main fat-phase antioxidant in cell membranes, protecting membrane lipids from oxidation.",
        "deficiency": "Rare from diet alone; shows up mainly in fat malabsorption, as nerve and muscle problems.",
        "reference": "Adult reference intake around 10–15 mg a day.",
        "detail": "The acetate ester is used for fortification because free tocopherol would be consumed protecting the food instead of the eater. Gut enzymes cleave the ester back to the active form.",
    },
    "vitamin_k": {
        "name": "Vitamin K",
        "emoji": "🥬",
        "patterns": [r"vitamin\s*k\s*[12]?\b", r"\bphylloquinone\b", r"\bmenaquinone\b"],
        "group": "Fat-soluble vitamin",
        "chemical": "Phylloquinone (K1) or menaquinones (K2)",
        "role": "Cofactor for the enzyme that activates clotting factors and several bone proteins.",
        "deficiency": "Impaired clotting and easy bruising; uncommon in adults eating normally.",
        "reference": "Adult reference intake around 55–120 µg a day.",
        "detail": "Gut bacteria produce some menaquinone, which supplements dietary intake. It interacts directly with warfarin-type anticoagulants, which work by blocking the same enzyme.",
    },
    "vitamin_c": {
        "name": "Vitamin C",
        "emoji": "🍊",
        "patterns": [r"vitamin\s*c\b", r"\bascorbic acid\b", r"\bsodium ascorbate\b"],
        "group": "Water-soluble vitamin",
        "chemical": "L-ascorbic acid, or its sodium or calcium salt",
        "role": "Cofactor for the enzymes that hydroxylate collagen, and a water-phase antioxidant. It also converts dietary iron to the form the gut absorbs more readily.",
        "deficiency": "Scurvy — bleeding gums, poor wound healing, fatigue.",
        "reference": "Adult reference intake around 40–90 mg a day.",
        "detail": "Heat- and oxygen-labile, so processed products are usually over-dosed at manufacture to allow for losses across shelf life. Most mammals synthesise their own; humans carry a broken copy of the final enzyme, which is why it is a vitamin for us at all.",
    },
    "vitamin_b1": {
        "name": "Vitamin B1 (Thiamine)",
        "emoji": "🌾",
        "patterns": [r"vitamin\s*b\s*-?\s*1\b", r"\bthiamin", r"\bb1\b"],
        "group": "Water-soluble vitamin (B-complex)",
        "chemical": "Thiamine mononitrate or thiamine hydrochloride",
        "role": "As thiamine pyrophosphate, the cofactor for the enzymes that feed carbohydrate into the citric acid cycle.",
        "deficiency": "Beriberi — nerve and heart involvement. Milled white rice and refined flour are low in it, which is why both are commonly fortified.",
        "reference": "Adult reference intake around 1.1–1.5 mg a day.",
        "detail": "The mononitrate form is chosen for dry fortification because it is far less hygroscopic than the hydrochloride. Requirement scales with carbohydrate intake — the more carbohydrate burned, the more thiamine needed to burn it.",
    },
    "vitamin_b2": {
        "name": "Vitamin B2 (Riboflavin)",
        "emoji": "🥛",
        "patterns": [r"vitamin\s*b\s*-?\s*2\b", r"\briboflavin\b", r"\bb2\b"],
        "group": "Water-soluble vitamin (B-complex)",
        "chemical": "Riboflavin, or riboflavin-5'-phosphate sodium for better solubility",
        "role": "Backbone of FAD and FMN, the electron carriers in energy metabolism.",
        "deficiency": "Cracks at the corners of the mouth, sore tongue, eye irritation.",
        "reference": "Adult reference intake around 1.1–1.7 mg a day.",
        "detail": "Intensely yellow and strongly light-sensitive — the reason milk in clear bottles loses both riboflavin and flavour quality. It also colours urine bright yellow at supplemental doses, which is harmless.",
    },
    "vitamin_b3": {
        "name": "Vitamin B3 (Niacin)",
        "emoji": "🥜",
        "patterns": [r"vitamin\s*b\s*-?\s*3\b", r"\bniacin", r"\bnicotinamide\b", r"\bb3\b"],
        "group": "Water-soluble vitamin (B-complex)",
        "chemical": "Nicotinamide (niacinamide) or nicotinic acid",
        "role": "Precursor of NAD and NADP, the hydrogen carriers used in several hundred metabolic reactions.",
        "deficiency": "Pellagra — dermatitis, diarrhoea, dementia.",
        "reference": "Adult reference intake around 14–18 mg niacin equivalents a day.",
        "detail": "Fortification uses nicotinamide rather than nicotinic acid because the acid form causes a harmless but uncomfortable skin flush at higher doses. The body can also make niacin from tryptophan, at roughly 60 mg tryptophan per 1 mg niacin.",
    },
    "vitamin_b5": {
        "name": "Vitamin B5 (Pantothenic acid)",
        "emoji": "💊",
        "patterns": [r"vitamin\s*b\s*-?\s*5\b", r"\bpantothen", r"\bb5\b"],
        "group": "Water-soluble vitamin (B-complex)",
        "chemical": "Calcium D-pantothenate",
        "role": "Built into coenzyme A, which carries acyl groups through fat and carbohydrate metabolism.",
        "deficiency": "Very rare — the vitamin is widespread in food, which is what the name means.",
        "reference": "Adult reference intake around 5 mg a day.",
        "detail": "The calcium salt is used for fortification because free pantothenic acid is an unstable, hygroscopic oil that would not survive in a dry mix.",
    },
    "vitamin_b6": {
        "name": "Vitamin B6 (Pyridoxine)",
        "emoji": "🐟",
        "patterns": [r"vitamin\s*b\s*-?\s*6\b", r"\bpyridoxin", r"\bb6\b"],
        "group": "Water-soluble vitamin (B-complex)",
        "chemical": "Pyridoxine hydrochloride",
        "role": "As pyridoxal phosphate, the cofactor for most amino acid transformations, plus haemoglobin and neurotransmitter synthesis.",
        "deficiency": "Irritability, confusion, a specific anaemia, cracked lips.",
        "reference": "Adult reference intake around 1.3–2.0 mg a day.",
        "detail": "One of the few water-soluble vitamins with a defined upper limit: sustained high supplemental intake can cause reversible sensory nerve damage. Ordinary fortification levels are nowhere near that range.",
    },
    "vitamin_b7": {
        "name": "Vitamin B7 (Biotin)",
        "emoji": "🥚",
        "patterns": [r"vitamin\s*b\s*-?\s*7\b", r"\bbiotin\b", r"\bb7\b"],
        "group": "Water-soluble vitamin (B-complex)",
        "chemical": "D-biotin",
        "role": "Cofactor for the carboxylase enzymes that build fatty acids and move carbon in gluconeogenesis.",
        "deficiency": "Rare — hair thinning, scaly rash, fatigue.",
        "reference": "Adult reference intake around 30–40 µg a day.",
        "detail": "Raw egg white contains avidin, a protein that binds biotin tightly and blocks its absorption; cooking denatures it. High-dose biotin supplements are known to skew several common laboratory blood tests.",
    },
    "vitamin_b9": {
        "name": "Vitamin B9 (Folic acid)",
        "emoji": "🥬",
        "patterns": [r"vitamin\s*b\s*-?\s*9\b", r"\bfolic acid\b", r"\bfolate\b", r"\bb9\b"],
        "group": "Water-soluble vitamin (B-complex)",
        "chemical": "Folic acid (pteroylmonoglutamic acid) — the stable synthetic form",
        "role": "Carries single-carbon units in DNA synthesis and repair, so it is needed wherever cells divide quickly.",
        "deficiency": "Megaloblastic anaemia; deficiency around conception raises the risk of neural tube defects.",
        "reference": "Adult reference intake around 300–400 µg dietary folate equivalents a day.",
        "detail": "Folic acid is more stable and better absorbed than the natural folates in food, which is why fortification and pregnancy supplements use it. Mandatory flour fortification in many countries exists specifically to reach pregnancies before they are recognised.",
    },
    "vitamin_b12": {
        "name": "Vitamin B12 (Cobalamin)",
        "emoji": "🧬",
        "patterns": [r"vitamin\s*b\s*-?\s*12\b", r"\bcobalamin\b", r"\bcyanocobalamin\b", r"\bb12\b"],
        "group": "Water-soluble vitamin (B-complex)",
        "chemical": "Cyanocobalamin, occasionally methylcobalamin",
        "role": "Required for red blood cell formation, for the myelin sheath around nerves, and alongside folate in DNA synthesis.",
        "deficiency": "Megaloblastic anaemia and, if prolonged, irreversible nerve damage.",
        "reference": "Adult reference intake around 2.2–2.4 µg a day.",
        "detail": "The only vitamin containing a metal ion — cobalt at the centre of a corrin ring. It is produced by bacteria, not plants or animals, so plant-based diets depend on fortified foods or supplements for it.",
    },
    "iron": {
        "name": "Iron",
        "emoji": "🩸",
        "patterns": [r"\biron\b", r"\bferrous\b", r"\bferric\b", r"\bferrous fumarate\b", r"\belemental iron\b"],
        "group": "Mineral (trace element)",
        "chemical": "Ferrous fumarate, ferrous sulphate, or ferric sodium EDTA (NaFeEDTA)",
        "role": "The oxygen-binding atom at the centre of haemoglobin and myoglobin, and part of the electron transport chain.",
        "deficiency": "Iron deficiency anaemia — fatigue, breathlessness, pallor. The single most common nutritional deficiency worldwide.",
        "reference": "Adult reference intake ranges widely — roughly 8–19 mg a day, and higher again for menstruating women under several national standards.",
        "detail": "Non-haem iron from fortification is absorbed far less efficiently than haem iron from meat. Vitamin C alongside it improves uptake; tea polyphenols, phytate from wholegrains, and calcium all reduce it. NaFeEDTA is used where the food matrix is high in phytate precisely because the chelate resists that binding.",
    },
    "calcium": {
        "name": "Calcium",
        "emoji": "🦴",
        "patterns": [r"\bcalcium\b"],
        "exclude_after": r"(carbonate|phosphate|silicate|sulphate|sulfate|lactate|chloride|oxide|stearate|propionate|citrate|pantothenate|hydroxide)",
        "group": "Mineral (macromineral)",
        "chemical": "Calcium carbonate or tricalcium phosphate in fortification",
        "role": "The mineral phase of bone and teeth; also central to muscle contraction, nerve signalling and clotting.",
        "deficiency": "Long-term shortfall contributes to low bone density and fracture risk.",
        "reference": "Adult reference intake around 800–1,000 mg a day.",
        "detail": "Absorption depends on vitamin D status, which is why the two are so often fortified together. The body defends blood calcium tightly, drawing on bone when intake falls short — so blood levels say little about whether intake is adequate.",
    },
    "zinc": {
        "name": "Zinc",
        "emoji": "⚙️",
        "patterns": [r"\bzinc\b"],
        "exclude_after": r"(oxide used|stearate)",
        "group": "Mineral (trace element)",
        "chemical": "Zinc sulphate, zinc oxide or zinc gluconate",
        "role": "Structural or catalytic component of well over two hundred enzymes, and needed for immune function, wound healing and taste perception.",
        "deficiency": "Poor growth, impaired immunity, slow wound healing, loss of taste.",
        "reference": "Adult reference intake around 11–17 mg a day.",
        "detail": "Phytate in wholegrains and legumes binds zinc and cuts absorption substantially, which is why cereal-based diets can fall short even when total intake looks adequate on paper.",
    },
    "iodine": {
        "name": "Iodine",
        "emoji": "🧂",
        "patterns": [r"\biodine\b", r"\biodi[sz]ed\b", r"\bpotassium iodate\b", r"\bpotassium iodide\b"],
        "group": "Mineral (trace element)",
        "chemical": "Potassium iodate (KIO3) — the form used in salt iodisation",
        "role": "The element the thyroid builds T3 and T4 from; those hormones set basal metabolic rate and drive brain development in early life.",
        "deficiency": "Goitre, hypothyroidism, and — if it occurs in pregnancy or infancy — permanent impairment of cognitive development.",
        "reference": "Adult reference intake around 150 µg a day; higher in pregnancy.",
        "detail": "Iodate is used rather than iodide because it survives heat, humidity and the impurities of unrefined salt far better. Universal salt iodisation is one of the most effective public-health nutrition measures on record.",
    },
    "magnesium": {
        "name": "Magnesium",
        "emoji": "🌰",
        "patterns": [r"\bmagnesium\b"],
        "exclude_after": r"(silicate|stearate|carbonate|oxide as anticaking)",
        "group": "Mineral (macromineral)",
        "chemical": "Magnesium oxide, citrate or sulphate",
        "role": "Cofactor for hundreds of enzymes, including every reaction that handles ATP — ATP is biologically active as its magnesium complex.",
        "deficiency": "Muscle cramps, irregular heartbeat, weakness.",
        "reference": "Adult reference intake around 310–420 mg a day.",
        "detail": "Roughly half the body's magnesium sits in bone. Absorption varies a lot by salt form, and the poorly absorbed forms are the ones that act as laxatives.",
    },
    "phosphorus": {
        "name": "Phosphorus",
        "emoji": "🦷",
        "patterns": [r"\bphosphorus\b"],
        "group": "Mineral (macromineral)",
        "chemical": "Present as phosphate salts",
        "role": "Part of bone mineral, of every phospholipid membrane, and of ATP and DNA themselves.",
        "deficiency": "Rare from diet — phosphorus is abundant in most foods.",
        "reference": "Adult reference intake around 700 mg a day.",
        "detail": "The concern with phosphorus in processed food runs the other way: phosphate additives are absorbed far more completely than the naturally bound phosphorus in whole foods, so intake can run high without appearing to.",
    },
    "selenium": {
        "name": "Selenium",
        "emoji": "🌰",
        "patterns": [r"\bselenium\b", r"\bsodium selenite\b"],
        "group": "Mineral (trace element)",
        "chemical": "Sodium selenite or selenomethionine",
        "role": "Built into the selenoproteins, including glutathione peroxidase, the enzyme family that clears peroxides from cells.",
        "deficiency": "Rare, and tied to selenium-poor soils; affects heart and joint tissue.",
        "reference": "Adult reference intake around 40–70 µg a day.",
        "detail": "It has one of the narrowest gaps between requirement and toxicity of any nutrient, so fortification levels are set conservatively.",
    },
    "potassium": {
        "name": "Potassium",
        "emoji": "🍌",
        "patterns": [r"\bpotassium\b"],
        "exclude_after": r"(sorbate|benzoate|chloride as|iodate|carbonate|citrate|metabisulphite|bitartrate|phosphate)",
        "group": "Mineral (macromineral)",
        "chemical": "Present as potassium salts",
        "role": "The main positive ion inside cells; it sets the membrane potential that nerve and muscle activity runs on.",
        "deficiency": "Weakness, cramps and heart rhythm disturbance, usually from losses rather than low intake.",
        "reference": "Adult reference intake around 3,500 mg a day.",
        "detail": "It works against sodium in blood pressure regulation, so the ratio between the two matters more than either figure alone.",
    },
}


# ============================================================
# INGREDIENT-TYPE TECHNICAL RULES
# ============================================================
# (pattern, chemical, family, source, detail, notes) — first match wins,
# so order these most-specific first.

TECH_RULES = [
    (
        r"\b(multigrain|multi grain)\b",
        "A blend of cereal flours — starch plus each grain's protein fraction",
        "Cereal flour blend",
        "Milled cereal grains, proportions stated on the label where declared",
        "A blend rather than a single flour, so the starch, protein and fibre profile is the weighted average of whatever is in it. The percentages in brackets are what actually determine the nutrition — a multigrain blend that is mostly refined wheat behaves nutritionally like refined wheat.",
        "",
    ),
    (
        r"\b(whole wheat|atta|wholemeal|whole grain wheat)\b",
        "Starch (amylose and amylopectin), gluten proteins gliadin and glutenin, plus bran and germ",
        "Wholegrain cereal flour",
        "Whole kernel of Triticum aestivum, milled with bran and germ retained",
        "Milled from the entire kernel, so it keeps the bran's insoluble fibre and the germ's oil and vitamin E. The retained germ oil is also why wholemeal flour goes rancid faster than refined flour.",
        "Contains gluten — a declarable allergen and unsuitable in coeliac disease.",
    ),
    (
        r"\b(maida|refined wheat flour|wheat flour|plain flour)\b",
        "Starch plus gluten-forming proteins (gliadin and glutenin)",
        "Refined cereal flour",
        "Endosperm of Triticum aestivum, bran and germ removed",
        "Only the starchy endosperm is retained, which removes most of the fibre, B vitamins and minerals along with the bran and germ. When hydrated and worked, gliadin and glutenin cross-link into the gluten network that traps gas and holds structure.",
        "Contains gluten — a declarable allergen and unsuitable in coeliac disease.",
    ),
    (
        r"\b(oat|oats|oat flour|rolled oats)\b",
        "Starch, oat protein (avenalin), and beta-glucan soluble fibre",
        "Wholegrain cereal",
        "Groats of Avena sativa, rolled or milled",
        "The distinguishing component is beta-glucan, a soluble fibre that forms a viscous solution in the gut. That viscosity is the mechanism behind the cholesterol claims permitted for oats in several jurisdictions.",
        "Often processed on shared lines with wheat, so 'may contain' gluten statements are common.",
    ),
    (
        r"\b(corn meal|cornmeal|maize flour|corn flour|maize|corn starch|corn)\b",
        "Maize starch, with zein as the main protein",
        "Cereal, ground",
        "Kernels of Zea mays",
        "Naturally gluten-free, and its protein zein forms no elastic network — which is why corn-based doughs are crumbly and need a binder. Coarser meal survives baking with a distinct crunch, which is often the reason it's in a biscuit at all.",
        "",
    ),
    (
        r"\b(rice flour|rice)\b",
        "Rice starch, low in protein, with no gluten-forming fraction",
        "Cereal, ground",
        "Grain of Oryza sativa",
        "Small starch granules and almost no functional protein, giving a light, crisp, sandy texture. Widely used to keep snacks crisp because rice starch retrogrades slowly.",
        "",
    ),
    (
        r"\b(sugar|sucrose|cane sugar)\b",
        "Sucrose — a disaccharide of glucose and fructose",
        "Nutritive sweetener",
        "Sugarcane or sugar beet, crystallised and refined",
        "Beyond sweetness it does structural work: it tenderises by competing with flour for water, lowers the freezing point in frozen products, browns via caramelisation and the Maillard reaction, and lowers water activity enough to slow microbial growth.",
        "Counts as free or added sugar for dietary guideline purposes, whatever the source.",
    ),
    (
        r"\b(invert syrup|golden syrup|glucose syrup|corn syrup|liquid glucose)\b",
        "Glucose, maltose and higher saccharides in solution",
        "Nutritive sweetener (syrup)",
        "Acid or enzyme hydrolysis of starch, usually maize",
        "Described by dextrose equivalent — how far the starch has been broken down. Low DE syrups thicken and add body; high DE syrups sweeten and brown. All of them hold moisture and slow sugar crystallisation.",
        "Counts as added sugar.",
    ),
    (
        r"\b(palm oil|palmolein|palm kernel|vanaspati|hydrogenated)\b",
        "Triglycerides, roughly 44% palmitic acid (C16:0) and 39% oleic acid (C18:1)",
        "Vegetable fat",
        "Mesocarp of the oil palm fruit, Elaeis guineensis",
        "Semi-solid at room temperature without hydrogenation, which is why it replaced partially hydrogenated fats when trans fat limits came in. Its high saturated fraction gives the snap and shelf stability that liquid oils can't.",
        "High in saturated fat. Fractionated palm stearin is higher still.",
    ),
    (
        r"\b(sunflower oil)\b",
        "Triglycerides, high in linoleic acid (C18:2), around 60% in the standard type",
        "Vegetable oil",
        "Seeds of Helianthus annuus",
        "The standard high-linoleic type is polyunsaturated and oxidises readily, so it is usually paired with an antioxidant. High-oleic cultivars swap most of that linoleic acid for oleic and are far more heat-stable.",
        "",
    ),
    (
        r"\b(soybean oil|soya oil|rapeseed|canola|cottonseed|groundnut oil|peanut oil|rice bran oil|vegetable oil|edible oil)\b",
        "A triglyceride mixture, fatty acid profile depending on the seed",
        "Vegetable oil",
        "Pressed or solvent-extracted oilseed, then refined, bleached and deodorised",
        "'Edible vegetable oil' as a declaration allows the manufacturer to switch between named oils as commodity prices move; any oil actually used has to be named in the bracket. Refining strips colour, free fatty acids and volatiles for a neutral, stable oil.",
        "",
    ),
    (
        r"\b(choco cream|chocolate cream|cocoa cream|cream filling|filling|creme|paste)\b",
        "A formulated mixture rather than a single compound — typically sugar, a vegetable fat and a flavour base",
        "Compound ingredient (prepared filling)",
        "Made separately by the manufacturer, then used as one ingredient",
        "A compound ingredient: it is itself a recipe, which is why the label declares its own sub-list in brackets. Regulations require that sub-list precisely because a shopper reading only the outer name would miss the sugar and fat inside it. The percentages inside the bracket are the ones worth reading.",
        "The sugar and fat in a product often sit inside a compound ingredient like this rather than in the top-level list.",
    ),
    (
        r"\b(cocoa solids|cocoa mass|cocoa powder|cocoa|choco|chocolate liquor)\b",
        "Non-fat cocoa solids — polyphenols, theobromine, caffeine and cocoa protein",
        "Cocoa ingredient",
        "Fermented, roasted and ground beans of Theobroma cacao",
        "Fermentation generates the flavour precursors and roasting develops them through the Maillard reaction. Dutching with alkali darkens the colour and softens acidity, at the cost of a large share of the flavanols.",
        "Contains theobromine and a little caffeine.",
    ),
    (
        r"\b(cocoa butter)\b",
        "Triglycerides dominated by stearic, palmitic and oleic acids",
        "Vegetable fat (cocoa)",
        "Pressed from cocoa beans",
        "Polymorphic — it can crystallise into six different forms. Only form V gives chocolate its gloss and snap, and producing it reliably is the whole point of tempering. Untempered chocolate sets into unstable forms that later migrate to form VI, the white bloom on old chocolate.",
        "",
    ),
    (
        r"\b(milk solids|skimmed milk powder|milk powder|whole milk|milk)\b",
        "Lactose, casein and whey proteins, milk fat, and milk minerals",
        "Dairy ingredient",
        "Cow's milk, evaporated and spray-dried",
        "Lactose browns readily via the Maillard reaction with milk proteins, which is where the cooked-dairy colour and flavour in biscuits comes from. Casein also emulsifies, helping fat stay dispersed.",
        "A declarable allergen. Contains lactose — relevant in lactose intolerance.",
    ),
    (
        r"\b(whey|whey protein|whey powder)\b",
        "Beta-lactoglobulin, alpha-lactalbumin, lactose and milk minerals",
        "Dairy ingredient",
        "Liquid left after casein is removed in cheesemaking, then dried",
        "Whey proteins denature and gel with heat, unlike casein, and they carry a high proportion of branched-chain amino acids — the reason whey dominates sports nutrition.",
        "A declarable dairy allergen.",
    ),
    (
        r"\b(iodi[sz]ed salt|iodi[sz]ed|salt|sodium chloride)\b",
        "Sodium chloride, with potassium iodate added where the salt is iodised",
        "Mineral seasoning",
        "Evaporated seawater or mined rock salt",
        "Beyond taste it tightens gluten, controls yeast activity in dough and lowers water activity. In the ingredient list it is the main contributor to the sodium figure in the nutrition panel — 1 g of salt is about 400 mg of sodium.",
        "Sodium intake is the practical thing to watch here rather than the salt itself.",
    ),
    (
        r"\b(lecithin)\b",
        "A phospholipid mixture, mainly phosphatidylcholine",
        "Emulsifier (phospholipid)",
        "Degummed soybean or sunflower oil, occasionally egg yolk",
        "Amphiphilic — a phosphate head that likes water, two fatty acid tails that like fat — so it parks at the interface and holds emulsions together. In chocolate it lowers viscosity by coating sugar crystals, letting the same flow be reached with less cocoa butter.",
        "Soy lecithin derives from a declarable allergen, though refining removes nearly all the protein.",
    ),
    (
        r"\b(natural flavour|nature identical|artificial flavour|flavour|flavor|essence|vanillin)\b",
        "A proprietary blend of aroma compounds, usually with a carrier solvent",
        "Flavouring preparation",
        "Extracted from a natural source, or synthesised to match one",
        "'Natural' as a regulatory term describes the origin of the molecules, not their simplicity — a natural flavour can still be a blend of dozens of isolated compounds. Exact composition is treated as trade secret, so labels declare only the class.",
        "Composition is not itemised on the label, which is a genuine limit on what can be known from the pack.",
    ),
    (
        r"\b(yeast extract)\b",
        "Autolysed yeast cell contents — free amino acids including glutamate, plus nucleotides",
        "Flavouring / flavour enhancer",
        "Saccharomyces cerevisiae, broken down by its own enzymes",
        "Delivers free glutamate and nucleotides, so it enhances savoury taste by the same receptor mechanism as MSG while being declarable as an ingredient rather than an additive.",
        "Naturally contains glutamate, relevant to anyone avoiding it.",
    ),
    (
        r"\b(yeast|baker's yeast)\b",
        "Live Saccharomyces cerevisiae",
        "Biological raising agent",
        "Propagated and dried or compressed",
        "Ferments dough sugars to carbon dioxide and ethanol. Unlike chemical leaveners it also builds flavour, because fermentation generates organic acids and esters over time — which is why slow-proved bread tastes of more.",
        "",
    ),
    (
        r"\b(starch|modified starch)\b",
        "Amylose and amylopectin glucose polymers",
        "Thickener / bulking agent",
        "Maize, tapioca, potato, rice or wheat",
        "Granules swell and burst on heating in water — gelatinisation — which is what thickens the system. On cooling the amylose re-associates and squeezes water out, the staling mechanism called retrogradation. Modification is chemistry aimed at stopping exactly that.",
        "Wheat-derived starch carries gluten traces unless specifically purified.",
    ),
    (
        r"\b(raising agent|baking powder|baking soda|sodium bicarbonate|ammonium bicarbonate)\b",
        "A carbonate or bicarbonate salt, usually with an acid salt partner",
        "Chemical raising agent",
        "Synthesised mineral salts",
        "Carbon dioxide is released when the carbonate meets acid or heat. Double-acting systems pair a fast acid that works in the bowl with a slow one that waits for oven heat, so the rise carries through to the bake.",
        "Adds a small amount of sodium where the sodium salt is used.",
    ),
    (
        r"\b(emulsifier)\b",
        "An amphiphilic molecule with a water-liking and a fat-liking end",
        "Emulsifier",
        "Usually derived from vegetable fats or phospholipids",
        "Works by lowering the interfacial tension between oil and water so droplets stay dispersed rather than coalescing. Which emulsifier is chosen depends on its hydrophilic-lipophilic balance, matched to whether the product is oil-in-water or water-in-oil.",
        "",
    ),
    (
        r"\b(antioxidant)\b",
        "A radical-scavenging compound, phenolic or tocopherol-type",
        "Antioxidant",
        "Synthesised, or isolated from vegetable oil",
        "Fats go rancid by a chain reaction: a radical pulls hydrogen off a fatty acid, creating another radical. A chain-breaking antioxidant donates its own hydrogen to end that chain, at the cost of being consumed itself.",
        "",
    ),
    (
        r"\b(preservative)\b",
        "An organic acid or its salt, in most cases",
        "Preservative",
        "Synthesised, sometimes matching a naturally occurring acid",
        "Weak-acid preservatives only work in the undissociated form, which crosses the microbial cell membrane and then dissociates inside, acidifying the cell. That is why they need an acidic product and do nothing much at neutral pH.",
        "",
    ),
    (
        r"\b(spice|masala|black pepper|pepper|chilli|chili|cumin|coriander|cardamom|clove|cinnamon|turmeric|paprika|oleoresin)\b",
        "A mixture of volatile essential oils and non-volatile pungent compounds",
        "Spice / seasoning",
        "Dried plant material — seed, bark, fruit or rhizome — ground or extracted",
        "Flavour sits in two fractions: volatile oils that give aroma and evaporate with heat and time, and non-volatile pungent principles like piperine or capsaicin that survive cooking. Oleoresin extracts capture both in a standardised, more stable form.",
        "",
    ),
    (
        r"\b(fruit|apple|mango|orange|berry|lemon|banana|date|raisin|grape|pineapple)\b",
        "Fruit sugars, pectin, organic acids, water and pigments",
        "Fruit ingredient",
        "Fruit, used whole, pureed, concentrated or dried",
        "Concentration and drying remove water, which raises the sugar density sharply — concentrated fruit purée and fruit juice concentrate are counted as free sugars in dietary guidelines for exactly that reason.",
        "Fruit concentrate counts as added sugar even though it comes from fruit.",
    ),
    (
        r"\b(tomato|onion|garlic|potato|carrot|spinach|pea|vegetable|beet)\b",
        "Vegetable solids — starch or fibre, with the plant's own sugars and pigments",
        "Vegetable ingredient",
        "Vegetables, used fresh, dried or as powder",
        "Dehydrated vegetable powders concentrate both flavour and any naturally present glutamate, which is why tomato and onion powder lift savoury character without a declared flavour enhancer.",
        "",
    ),
    (
        r"\b(peanut|almond|cashew|walnut|pistachio|hazelnut|nuts?)\b",
        "Storage lipid, protein and fibre; peanut is a legume despite the name",
        "Nut ingredient",
        "Tree nuts or groundnuts, whole, roasted, pasted or as flour",
        "Roasting drives Maillard browning between nut proteins and sugars, which is where roasted flavour comes from. The high unsaturated fat content is also why nut products go rancid comparatively quickly.",
        "A declarable allergen, and one of the most common causes of severe food allergy.",
    ),
    (
        r"\b(soy|soya|soybean)\b",
        "Soy protein, soybean oil, and oligosaccharides",
        "Legume ingredient",
        "Seeds of Glycine max",
        "One of the few plant proteins with a complete essential amino acid profile, which is why it substitutes for dairy and meat protein in formulated foods.",
        "A declarable allergen.",
    ),
    (
        r"\b(egg|albumen|egg white|egg yolk)\b",
        "Ovalbumin and other egg proteins; yolk adds lecithin and lipid",
        "Egg ingredient",
        "Hen eggs, liquid, dried or frozen",
        "Does three jobs at once: white proteins coagulate and set structure, yolk lecithin emulsifies, and whipped white holds air as a protein foam.",
        "A declarable allergen.",
    ),
    (
        r"\b(water|aqua)\b",
        "H2O",
        "Solvent / carrier",
        "Potable water, treated to process specification",
        "Its real significance on a label is water activity — how much of it is chemically available to microbes rather than bound to sugar, salt or starch. That figure, not total moisture, decides shelf life.",
        "",
    ),
    (
        r"\b(gelatin|gelatine)\b",
        "Partially hydrolysed collagen",
        "Gelling agent",
        "Animal hide and bone, usually bovine or porcine",
        "Melts near body temperature, which is the source of the melt-in-the-mouth quality no plant gum reproduces. Gel strength is graded in Bloom.",
        "Animal-derived — not vegetarian. The source species is not always declared.",
    ),
    (
        r"\b(honey)\b",
        "Fructose and glucose, with trace enzymes, acids and aroma compounds",
        "Nutritive sweetener",
        "Nectar processed by honeybees",
        "Higher fructose than sucrose, so sweeter per gram and strongly hygroscopic — it holds moisture in a bake. Nutritionally it is still a free sugar.",
        "Counts as added sugar. Not given to infants under one year because of botulism spore risk.",
    ),
    (
        r"\b(caffeine)\b",
        "Caffeine — 1,3,7-trimethylxanthine",
        "Stimulant / flavouring",
        "Extracted from coffee, tea or kola, or synthesised",
        "A purine alkaloid that works by blocking adenosine receptors, the signal that normally accumulates as tiredness through the day. Also mildly bitter, so it contributes to the taste profile of cola drinks as well as the effect.",
        "Labels are required to carry a caffeine advisory in many countries, typically aimed at children and pregnant women.",
    ),
    (
        r"\b(malt extract|barley malt|malted|malt)\b",
        "Maltose, glucose, dextrins and amino acids, with malt-derived enzymes",
        "Cereal extract",
        "Germinated barley, dried and extracted with water",
        "Germination activates amylases that break the grain's starch into fermentable sugars; the extract is that sugar syrup concentrated. It brings its own colour and roasted flavour from the kilning step, which is why it is used for taste as much as sweetness.",
        "Barley-derived, so it contains gluten. Counts as a source of sugars.",
    ),
    (
        r"\b(wheat gluten|vital gluten|gluten)\b",
        "Gliadin and glutenin, the two storage proteins of wheat",
        "Protein (dough strengthener)",
        "Washed out of wheat flour and dried",
        "Added back to flour to raise the total protein when the base flour is too weak to hold gas — the mechanism behind high-rise industrial bread and most wholemeal loaves, where bran would otherwise cut the gluten network.",
        "A declarable allergen and unsuitable in coeliac disease.",
    ),
    (
        r"\b(butter|ghee|milk fat|butterfat|butter oil|cream)\b",
        "Milk fat — a triglyceride mixture with a wide melting range, plus short-chain fatty acids",
        "Dairy fat",
        "Churned or clarified cream from cow's milk",
        "Its mixture of fatty acid chain lengths means it softens gradually rather than melting at one temperature, which is what gives butter-based baking its texture. Clarifying to ghee drives off water and milk solids, raising the smoke point and the keeping quality.",
        "A dairy ingredient. High in saturated fat.",
    ),
    (
        r"\b(anticaking|anti[\s-]?caking|free[\s-]?flow)\b",
        "A fine inert mineral powder, typically a silicate",
        "Anticaking agent",
        "Mined or synthesised mineral",
        "Coats the surface of larger particles and adsorbs the surface moisture that would otherwise bridge them together, so powders and granules keep pouring.",
        "",
    ),
    (
        r"\b(humectant)\b",
        "A water-binding compound — commonly a polyol or a phosphate salt",
        "Humectant",
        "Synthesised",
        "Holds water in the product by binding it chemically. That lowers water activity — the share of moisture actually available to microbes — so the food stays soft without becoming a better place for spoilage organisms to grow.",
        "",
    ),
    (
        r"\b(acidity regulator|acid regulator|ph regulator|acidulant)\b",
        "An organic or mineral acid, or its buffering salt",
        "Acidity regulator",
        "Fermentation or synthesis",
        "Holds the product at a set pH. That matters for more than taste: pH decides whether pectin gels, whether proteins stay dissolved, what colour anthocyanins take, and how well weak-acid preservatives work.",
        "",
    ),
    (
        r"\b(flavour enhancer|flavor enhancer)\b",
        "A free glutamate salt, a 5'-nucleotide, or a blend of both",
        "Flavour enhancer",
        "Fermentation",
        "Binds umami taste receptors directly rather than adding a flavour of its own. Glutamates and nucleotides are strongly synergistic — together they register far stronger than the sum of the two used separately, which is why labels usually declare them as a pair.",
        "",
    ),
    (
        r"\b(stabilis|stabiliz|thickener|thickening)\b",
        "A hydrocolloid — a long-chain polysaccharide that binds water",
        "Stabiliser / thickener",
        "Plant, seaweed or fermentation-derived gum, or modified starch",
        "Hydrocolloids thicken by trapping water in a polymer network rather than by dissolving into it. Which one is chosen depends on the texture wanted: some give a clinging body, some a brittle gel, some hold their viscosity at rest but thin the moment they are poured.",
        "",
    ),
    (
        r"\b(colour|color|permitted colour)\b",
        "A pigment — synthetic dye, mineral, or plant-derived extract",
        "Colour",
        "Synthesis, mineral processing, or plant extraction",
        "Added for appearance only; it contributes nothing to taste, texture or nutrition. Synthetic dyes are chosen where the shade has to survive heat, light and acid unchanged, which is where most natural pigments fail.",
        "Several synthetic colours carry advisory labelling in the EU regarding activity and attention in children.",
    ),
    (
        r"\b(sweetener)\b",
        "Either a high-intensity sweetener used in milligrams, or a bulk polyol",
        "Sweetener",
        "Synthesis, fermentation, or plant extraction",
        "High-intensity sweeteners are hundreds of times sweeter than sugar, so they are used in trace amounts and contribute no bulk — which is why sugar-free products usually need a polyol or a fibre alongside to replace the body sugar would have given.",
        "Polyols are incompletely absorbed and can be laxative in quantity.",
    ),
    (
        r"\b(vitamins?|minerals?|fortif|premix)\b",
        "Added vitamin and mineral compounds in their stable fortification forms",
        "Fortificant",
        "Synthesised or fermentation-derived nutrient premix",
        "Fortification uses specific chemical forms chosen for stability in a dry mix and for bioavailability — thiamine mononitrate rather than the hydrochloride, ferrous fumarate rather than free iron. Levels are usually over-dosed at manufacture to allow for degradation across shelf life.",
        "",
    ),
]

COMPILED_TECH_RULES = [
    (re.compile(pattern, re.IGNORECASE), chemical, family, source, detail, notes)
    for pattern, chemical, family, source, detail, notes in TECH_RULES
]

CATEGORY_FALLBACK = {
    "sweetener": ("A carbohydrate sweetener", "Nutritive sweetener", "Refined from a plant sugar source"),
    "grain": ("Starch with the grain's own protein fraction", "Cereal ingredient", "Milled cereal grain"),
    "oil or fat": ("A triglyceride mixture", "Fat or oil", "Pressed or refined from a plant or animal source"),
    "dairy": ("Milk proteins, milk fat and lactose", "Dairy ingredient", "Cow's milk, processed"),
    "allergen": ("A protein-bearing ingredient from a declarable allergen source", "Allergen-bearing ingredient", "Named on the label"),
    "spice": ("Volatile aroma oils plus non-volatile pungent compounds", "Spice or seasoning", "Dried plant material"),
    "colour": ("A pigment compound", "Colour", "Plant, mineral or synthetic"),
    "preservative": ("An antimicrobial acid or salt", "Preservative", "Synthesised"),
    "emulsifier": ("An amphiphilic surface-active molecule", "Emulsifier", "Derived from fats or phospholipids"),
    "protein": ("Concentrated protein, isolated from its source matrix", "Protein ingredient", "Plant or dairy extraction"),
    "vitamin or mineral": ("Added nutrient compounds in fortification form", "Fortificant", "Nutrient premix"),
}


# ============================================================
# INS CODE HANDLING
# ============================================================

INS_PATTERN = re.compile(r"\b(?:INS|E)[\s.\-]?(\d{3,4})\s*\(?\s*([ivx]{1,4}|[a-d])?\s*\)?", re.IGNORECASE)

ROMAN_SUFFIX = {"i": "i", "ii": "ii", "iii": "iii", "iv": "iv", "v": "v"}


def find_ins_code(text):
    """Return (display_code, lookup_keys) for the first INS/E code in the text."""

    match = INS_PATTERN.search(text or "")

    if not match:
        return "", []

    number = match.group(1)
    suffix = (match.group(2) or "").lower()

    if suffix in ROMAN_SUFFIX:
        display = f"INS {number}({suffix})"
        keys = [f"{number}{suffix}", number]
    elif suffix:
        display = f"INS {number}{suffix}"
        keys = [f"{number}{suffix}", number]
    else:
        display = f"INS {number}"
        keys = [number]

    return display, keys


def ins_entry(keys):
    for key in keys:
        entry = INS_TECHNICAL.get(key)
        if entry:
            return entry
    return None


# ============================================================
# MICRONUTRIENT MATCHING
# ============================================================

COMPILED_MICRO = {
    key: [re.compile(pattern, re.IGNORECASE) for pattern in info["patterns"]]
    for key, info in MICRONUTRIENTS.items()
}


def _mineral_is_additive_use(text, key):
    """
    'Calcium carbonate' as an anticaking agent is not a calcium fortificant,
    and flagging it as one would be misleading. Minerals carrying an
    `exclude_after` pattern only count when the word stands on its own.
    """

    info = MICRONUTRIENTS.get(key, {})
    exclusion = info.get("exclude_after")

    if not exclusion:
        return False

    word = info["name"].split()[0]
    nearby = re.search(rf"\b{re.escape(word)}\s+(\w+)", text, re.IGNORECASE)

    if not nearby:
        return False

    return bool(re.match(exclusion, nearby.group(1), re.IGNORECASE))


def match_micronutrient(text):
    """Return the first micronutrient id the text names, or None."""

    if not text:
        return None

    for key, patterns in COMPILED_MICRO.items():
        for pattern in patterns:
            if pattern.search(text):
                if _mineral_is_additive_use(text, key):
                    continue
                return key

    return None


# ============================================================
# TECHNICAL PROFILE FOR ONE INGREDIENT
# ============================================================

def technical_profile(simple_name="", original="", category=""):
    """
    Build the technical block for a single ingredient card.

    Returns a dict with chemical / ins / family / source / detail / notes.
    Empty strings where nothing useful is known — the front end hides
    those rows rather than printing a placeholder.
    """

    # A compound ingredient declares its own sub-list — "Choco Cream (Sugar,
    # Edible Vegetable Oil (...), Emulsifier (INS 322(i)), Flavour)". Matching
    # against that whole string would describe the card by whatever happens to
    # sit deepest inside its brackets, so the card's own name is tried first
    # and the full declaration only as a fallback, and only when it is short
    # enough to be a plain ingredient rather than a nested list.
    name = (simple_name or "").strip()
    printed = (original or "").strip()
    extended = printed if len(printed) <= 70 else ""

    haystacks = [h for h in (name, f"{name} {extended}".strip()) if h]

    profile = {"chemical": "", "ins": "", "family": "", "source": "", "detail": "", "notes": ""}

    # 1. INS / E number stated on the label
    display_code, keys = "", []

    for haystack in haystacks:
        display_code, keys = find_ins_code(haystack)
        if display_code:
            break

    if display_code:
        profile["ins"] = display_code
        entry = ins_entry(keys)

        if entry:
            profile.update(
                {
                    "chemical": entry["chemical"],
                    "family": entry["family"],
                    "source": entry["source"],
                    "detail": entry["detail"],
                    "notes": entry["notes"],
                }
            )
            return profile

    # Code present but not in the table — say so plainly rather than
    # inventing chemistry for a number we don't recognise. Held aside so a
    # function-level rule below can still describe what the additive does.
    code_note = ""

    if display_code and not profile["chemical"]:
        code_note = (
            f"The label gives only the code {display_code}; the specific compound behind it "
            "isn't in this reference table, so the declared function is the most reliable guide."
        )

    # 2 + 3. A named nutrient, or a recognised ingredient type.
    #
    # These two are resolved together because they overlap: iodised salt
    # matches both the iodine entry and the salt rule, and the salt rule is
    # the honest description of the ingredient. So a specific ingredient rule
    # wins — unless the only rule that matched is the catch-all fortificant
    # one, in which case the named nutrient is the more useful answer.
    micro_key = None
    rule_hit = None

    for haystack in haystacks:
        micro_key = micro_key or match_micronutrient(haystack)

        if not rule_hit:
            for pattern, chemical, family, source, detail, notes in COMPILED_TECH_RULES:
                if pattern.search(haystack):
                    rule_hit = (chemical, family, source, detail, notes)
                    break

        if micro_key and rule_hit:
            break

    generic_rule = bool(rule_hit) and rule_hit[1] == "Fortificant"

    if micro_key and (not rule_hit or generic_rule):
        info = MICRONUTRIENTS[micro_key]
        if not profile["chemical"]:
            profile["chemical"] = info["chemical"]
            profile["family"] = info["group"]
            profile["source"] = "Added as a nutrient premix"
            profile["detail"] = info["detail"]
        if code_note:
            profile["notes"] = f"{profile['notes']} {code_note}".strip()
        return profile

    if rule_hit:
        chemical, family, source, detail, notes = rule_hit
        if not profile["chemical"]:
            profile["chemical"] = chemical
            profile["family"] = family
            profile["source"] = source
            profile["detail"] = detail
            profile["notes"] = notes
        elif not profile["notes"]:
            profile["notes"] = notes

        if code_note:
            profile["notes"] = f"{profile['notes']} {code_note}".strip()

        return profile

    if code_note:
        profile["detail"] = code_note

    # 4. Category-level fallback
    fallback = CATEGORY_FALLBACK.get((category or "").strip().lower())

    if fallback and not profile["chemical"]:
        chemical, family, source = fallback
        profile["chemical"] = chemical
        profile["family"] = family
        profile["source"] = source

    return profile


def attach_technical(items):
    """
    Add a `technical` block to every ingredient card, in place.

    Runs on both the AI cards and the offline cards, so the shape the front
    end receives is identical either way. Anything the AI already filled in
    is kept; this only fills the gaps.
    """

    for item in items or []:
        existing = item.get("technical")
        existing = existing if isinstance(existing, dict) else {}

        computed = technical_profile(
            item.get("simple_name", ""),
            item.get("original", ""),
            item.get("category", ""),
        )

        merged = {}
        for field in ("chemical", "ins", "family", "source", "detail", "notes"):
            value = str(existing.get(field) or "").strip()
            merged[field] = value or computed[field]

        item["technical"] = merged

    return items


# ============================================================
# PULLING VITAMINS AND MINERALS OFF THE LABEL
# ============================================================

VITAMIN_BLOCK = re.compile(
    r"vitamins?(?:\s*(?:&|and)\s*minerals?)?\s*[:\-]?\s*(\([^)]*\)|\{[^}]*\}|\[[^\]]*\])?",
    re.IGNORECASE,
)

AMOUNT_NEAR = re.compile(
    r"(\d+(?:\.\d+)?)\s*(mcg|µg|ug|mg|g|iu|%)", re.IGNORECASE
)


def _stated_amount(text, patterns):
    """If the label prints a figure right after the nutrient name, keep it."""

    # A nutrient is usually named twice — once in the ingredient list with no
    # figure, once in the nutrition panel with one. Check every mention and
    # keep the first that actually carries an amount.
    candidates = [match for pattern in patterns for match in pattern.finditer(text)]

    for match in candidates:
        window = text[match.end() : match.end() + 34]

        # Stop at the boundary of the next declaration, or "Calcium)." picks
        # up the figure printed against whatever nutrient comes after it.
        boundary = re.search(r"[,;:)\]}\n]|\.\s", window)
        if boundary:
            window = window[: boundary.start()]

        amount = AMOUNT_NEAR.search(window)

        if amount:
            unit = amount.group(2).lower()
            unit = "µg" if unit in {"mcg", "ug"} else unit
            unit = "IU" if unit == "iu" else unit
            separator = "" if unit == "%" else " "
            return f"{amount.group(1)}{separator}{unit}"

    return ""


def extract_micronutrients(raw_text, items=None):
    """
    Find every vitamin and mineral the label names, wherever it names it —
    inside the ingredient list, inside a bracketed vitamin blend, or only
    in the nutrition panel.

    Returns a list of cards, plus a `declared_unnamed` marker when the pack
    says "vitamins" without saying which.
    """

    items = items or []
    haystacks = [raw_text or ""]

    for item in items:
        haystacks.append(f"{item.get('simple_name','')} {item.get('original','')}")

    combined = " ".join(haystacks)

    found = []
    seen = set()

    for key, patterns in COMPILED_MICRO.items():
        if not any(pattern.search(combined) for pattern in patterns):
            continue
        if _mineral_is_additive_use(combined, key):
            continue
        if key in seen:
            continue

        seen.add(key)
        info = MICRONUTRIENTS[key]

        found.append(
            {
                "key": key,
                "name": info["name"],
                "emoji": info["emoji"],
                "group": info["group"],
                "chemical": info["chemical"],
                "role": info["role"],
                "deficiency": info["deficiency"],
                "reference": info["reference"],
                "detail": info["detail"],
                "amount": _stated_amount(combined, patterns),
            }
        )

    # Keep them in the order the table defines: fat-soluble, water-soluble,
    # then minerals — which reads better than OCR order.
    order = list(MICRONUTRIENTS.keys())
    found.sort(key=lambda card: order.index(card["key"]))

    declared_unnamed = False
    block = VITAMIN_BLOCK.search(combined)

    if block and not found:
        declared_unnamed = True
    elif block and not (block.group(1) or "").strip() and len(found) <= 1:
        # "VITAMINS" with nothing in brackets and nothing else identified.
        declared_unnamed = True

    return {"items": found, "declared_unnamed": declared_unnamed}
