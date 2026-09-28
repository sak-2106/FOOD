"""
knowledge.py
============

ingredients_db.json only has exact entries for ~90 named ingredients.
Real labels contain far more than that — regional spice blends, specific
flour types, brand-specific additive names. Previously, anything outside
the database fell back to a placeholder like "Listed by weight" instead of
an actual explanation.

This module is the middle tier: a set of ~35 pattern rules that recognise
the *kind* of ingredient (a raising agent, an emulsifier, a natural gum,
a flour, a spice...) from its name, even when the exact ingredient has no
database entry, and produce a real, specific "what it is" / "why it's used"
pair instead of a non-answer.

Lookup order, applied in simplifier.py:

    1. ingredients_db.json   — exact name/alias match (most specific)
    2. INS/E-number pattern  — known additive code
    3. knowledge.py rules    — recognised ingredient *type* (this file)
    4. generic positional filler — only when nothing above matches at all
"""

import re

# Each rule: (pattern, category, what, why, watch-or-"")
# Ordered most-specific first; the first match wins.

RULES = [
    (
        r"\b(raising agent|baking soda|sodium bicarbonate|ammonium bicarbonate)\b",
        "Raising agent",
        "A raising agent, most likely a form of baking soda or a related compound, that releases gas when heated or mixed with liquid.",
        "Makes the dough or batter rise and gives a lighter texture.",
        "",
    ),
    (
        r"\bemulsifier\b",
        "Emulsifier",
        "An emulsifier — a compound that lets fat and water mix smoothly instead of separating.",
        "Keeps the texture even, so the product doesn't turn oily or watery over time.",
        "",
    ),
    (
        r"\b(stabilis|stabiliz|thicken|gum\b)",
        "Stabiliser",
        "A stabiliser or thickener, usually a plant gum or starch derivative, that keeps a food's texture consistent.",
        "Stops ingredients separating and gives a smoother, thicker texture.",
        "",
    ),
    (
        r"\b(acidity regulator|acid regulator|ph regulator)\b",
        "Acidity regulator",
        "An acidity regulator, used to keep the product at a consistent, safe level of acidity.",
        "Controls tartness and helps preserve the product.",
        "",
    ),
    (
        r"\b(flavour enhancer|flavor enhancer|monosodium|disodium)\b",
        "Flavor enhancer",
        "A flavour enhancer that makes existing savoury or sweet notes taste stronger, without adding much flavour of its own.",
        "Makes the overall taste more pronounced.",
        "Some people are sensitive to flavour enhancers like this one.",
    ),
    (
        r"\bantioxidant\b",
        "Antioxidant",
        "An antioxidant added to stop the fats in this product reacting with oxygen and turning rancid.",
        "Extends shelf life by keeping the product tasting fresh for longer.",
        "",
    ),
    (
        r"\b(preservative|sorbate|benzoate|propionate|sulphite|sulfite|nitrite|nitrate)\b",
        "Preservative",
        "A preservative, used to slow down the growth of bacteria, yeast or mould.",
        "Extends how long the product stays safe and fresh to eat.",
        "A processing additive rather than a food in its own right.",
    ),
    (
        r"\b(colour|color|tartrazine|carmoisine|sunset yellow|allura|caramel|annatto)\b",
        "Colour",
        "A colouring added purely to change how the product looks, with no flavour or nutritional role.",
        "Makes the product's appearance more consistent or more appealing.",
        "An artificial colour, added purely for appearance.",
    ),
    (
        r"\b(essence|flavour|flavor|aroma)\b",
        "Flavouring",
        "A flavouring compound, either extracted from a natural source or made synthetically to match one.",
        "Gives the product a consistent taste from batch to batch.",
        "",
    ),
    (
        r"\b(sweetener|syrup|dextrose|fructose|glucose|maltose|jaggery|honey|molasses)\b",
        "Sweetener",
        "A sweetening ingredient — most likely a form of sugar or a sugar syrup.",
        "Adds sweetness and, in syrup form, can also affect texture and shelf life.",
        "This is a form of added sugar.",
    ),
    (
        r"\b(vitamins?|minerals?|fortif|iron\b|zinc\b|calcium\b|iodi[sz]ed)\b",
        "Vitamin or mineral",
        "A vitamin or mineral added to fortify the product, topping up a nutrient that might otherwise be low in the diet.",
        "Nutritional fortification, often required or encouraged by local food regulations.",
        "",
    ),
    (
        r"\b(choco cream|chocolate cream|cocoa cream|cream filling|creme)\b",
        "Compound ingredient",
        "A prepared filling the manufacturer makes separately and then uses as a single ingredient — which is why it carries its own list in brackets.",
        "Provides the sweet, fatty filling layer rather than doing one specific job.",
        "Much of a product's sugar and fat can sit inside a compound ingredient like this.",
    ),
    (
        r"\b(multigrain|multi grain|flour mix|flour blend|grain mix)\b",
        "Grain",
        "A blend of cereal flours rather than a single one, with the proportions given in the brackets next to it.",
        "Provides the bulk, structure and carbohydrate the product is built on.",
        "",
    ),
    (
        r"\b(wheat|atta|maida|semolina|barley|rye|oat|millet|bajra|jowar|ragi)\b",
        "Grain",
        "A cereal grain or flour milled from it, forming part of the carbohydrate base of the product.",
        "Provides bulk, structure and carbohydrate energy.",
        "",
    ),
    (
        r"\b(rice|corn|maize)\b",
        "Grain",
        "A grain-based ingredient — rice or corn, either whole, ground into flour, or as a starch.",
        "Provides bulk, texture or thickening.",
        "",
    ),
    (
        r"\b(milk|dairy|whey|casein|curd|paneer|cream\b|cheese)\b",
        "Dairy",
        "A milk-derived ingredient, likely providing protein, fat or a creamy texture.",
        "Adds richness, protein and helps with browning.",
        "A common allergen — skip it if you react to dairy.",
    ),
    (
        r"\b(oil|ghee|fat\b|shortening|margarine)\b",
        "Oil or fat",
        "A cooking fat or oil, used as one of the main sources of energy and texture in the product.",
        "Adds richness, helps with browning, and keeps the product moist.",
        "",
    ),
    (
        r"\bsalt\b",
        "Other",
        "Ordinary table salt, or a close variant of it such as iodised salt.",
        "Adds savoury flavour and, in small amounts, helps preserve the product.",
        "",
    ),
    (
        r"\bwater\b",
        "Other",
        "Water, used as the base liquid the other ingredients are mixed or dissolved into.",
        "Adjusts texture and helps other ingredients combine.",
        "",
    ),
    (
        r"\b(peanut|almond|cashew|walnut|pistachio|hazelnut|nuts?)\b",
        "Allergen",
        "A nut or nut-derived ingredient.",
        "Adds flavour, crunch or richness.",
        "A common allergen — this one is worth checking if you have a nut allergy.",
    ),
    (
        r"\b(soy|soya|lecithin)\b",
        "Allergen",
        "A soy-derived ingredient, often used for its fat or emulsifying properties.",
        "Helps bind fat and water together and improves texture.",
        "A common allergen.",
    ),
    (
        r"\begg\b",
        "Allergen",
        "An egg-derived ingredient, likely from whole egg, egg white or egg yolk.",
        "Binds ingredients together and adds structure or richness.",
        "A common allergen.",
    ),
    (
        r"\b(fish|shellfish|crab|shrimp|prawn|anchovy)\b",
        "Allergen",
        "A fish or shellfish-derived ingredient.",
        "Adds savoury depth of flavour.",
        "A common allergen.",
    ),
    (
        r"\b(sesame|mustard)\b",
        "Allergen",
        "A sesame or mustard-derived ingredient.",
        "Adds flavour and, in the case of mustard, some emulsifying properties.",
        "A common allergen in many countries.",
    ),
    (
        r"\byeast\b",
        "Raising agent",
        "A living microorganism that ferments sugars in the dough and releases carbon dioxide.",
        "Makes bread and similar baked goods rise.",
        "",
    ),
    (
        r"\b(cocoa|choco|chocolate)\b",
        "Flavouring",
        "A cocoa-derived ingredient, giving the product its chocolate flavour and colour.",
        "Adds chocolate flavour.",
        "",
    ),
    (
        r"\b(spice|masala|pepper|chilli|chili|cumin|coriander|ginger|cardamom|clove|cinnamon|turmeric|paprika)\b",
        "Spice",
        "A spice or spice blend, used to season the product.",
        "Adds flavour and aroma.",
        "",
    ),
    (
        r"\b(fruit|apple|mango|orange|berry|lemon|banana|date\b|raisin|grape)\b",
        "Fruit",
        "A fruit-derived ingredient — likely the fruit itself, a puree, or a concentrated extract of it.",
        "Adds natural sweetness, flavour and colour.",
        "",
    ),
    (
        r"\b(tomato|onion|garlic|potato|carrot|spinach|pea\b|vegetable|beet)\b",
        "Vegetable",
        "A vegetable-derived ingredient, used for flavour, colour or bulk.",
        "Adds savoury flavour and texture.",
        "",
    ),
    (
        r"\b(protein|isolate|concentrate)\b",
        "Protein",
        "A concentrated protein ingredient, extracted from a plant or dairy source.",
        "Boosts the protein content of the product.",
        "",
    ),
    (
        r"\bstarch\b",
        "Carbohydrate / Food additive",
        "A refined starch, extracted from a grain, root or tuber.",
        "Thickens the mixture or acts as an anti-caking agent.",
        "",
    ),
]

COMPILED_RULES = [(re.compile(pattern, re.IGNORECASE), category, what, why, watch) for pattern, category, what, why, watch in RULES]


def lookup(normalized_name, original):
    """
    Returns (category, what, why, watch) for the first rule that matches
    the normalized ingredient name, or None if nothing matches.
    """

    haystack = f"{normalized_name} {original}"

    for pattern, category, what, why, watch in COMPILED_RULES:
        if pattern.search(haystack):
            return category, what, why, watch

    return None
