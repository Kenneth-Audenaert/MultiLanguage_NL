-- MultiLanguage_NL: Dutch language pack for MultiLanguage + TTS support
-- Author: Kenneth Audenaert

local addonName = ...

-- ─── Taalregistratie (zelfde patroon als MultiLanguage_DE) ──────────────────
local optionsFrame = CreateFrame("Frame")

local function languageExists(value)
    for _, language in ipairs(MultiLanguageOptions.AVAILABLE_LANGUAGES) do
        if language.value == value then
            return true
        end
    end
    return false
end

local function addLanguageOption()
    if not languageExists("nl") then
        table.insert(MultiLanguageOptions.AVAILABLE_LANGUAGES, {value = "nl", text = "Dutch"})
        AddLanguageDropdownOption()
    end
end

local function addAlwaysInteraction()
    if not MultiLanguageOptions or not MultiLanguageOptions.AVAILABLE_INTERACTIONS then return end
    for _, interaction in ipairs(MultiLanguageOptions.AVAILABLE_INTERACTIONS) do
        if interaction.value == "always" then return end
    end
    table.insert(MultiLanguageOptions.AVAILABLE_INTERACTIONS, {value = "always", text = "Always show"})
end

-- UI-labels die de hoofd-addon nodig heeft voor het vertaalframe
local function registerTranslations()
    if not _G["MultiLanguageTranslations"] then
        _G["MultiLanguageTranslations"] = {}
    end
    _G["MultiLanguageTranslations"]["nl"] = {
        description = "Beschrijving",
        objectives = "Quest Doelstellingen",
        options = {
            generalOptionsTitle = "Algemene opties",
            languageDropdownLabel = "Selecteer taal:",
            interactionDropdownLabel = "Selecteer interactie:",
            registerHotkeyDescriptionText = "Sneltoets registreren (rechtsklik om te ontbinden):",
            registerHotkeyNotBoundText = "Niet toegewezen",
            registerHotkeyPressButtonText = "Druk op een toets...",
            questOptionsTitle = "Quest opties",
            questDisplayModeText = "Selecteer quest weergavemodus:",
            itemOptionsTitle = "Item opties",
            spellOptionsTitle = "Spreuk opties",
            npcOptionsTitle = "NPC opties",
            enableText = "Inschakelen",
            onlyDisplayNameText = "Alleen naam weergeven",
            languages = {
                en = "Engels",
                es = "Spaans",
                fr = "Frans",
                de = "Duits",
                pt = "Portugees",
                ru = "Russisch",
                ko = "Koreaans",
                cn = "Chinees (vereenvoudigd)",
                mx = "Spaans (Mexico)",
                tw = "Chinees (traditioneel)",
                nl = "Nederlands"
            },
            interactionModes = {
                hover = "Aanwijzen",
                ["hover-hotkey"] = "Aanwijzen + sneltoets",
                always = "Altijd tonen"
            }
        }
    }
end

-- ─── Injecteer NL vertalingen in alle bestaande taaltabellen ─────────────────
-- De dropdown haalt tekst op via optionsTranslations["languages"][value] en
-- optionsTranslations["interactionModes"][value], dus elke taal moet "nl" en
-- "always" kennen, anders wordt de tekst nil (leeg in de dropdown).
local function injectIntoAllTranslations()
    local translations = _G["MultiLanguageTranslations"]
    if not translations then return end

    local nlLanguageNames = {
        en = "Dutch", es = "Neerlandés", de = "Niederländisch",
        fr = "Néerlandais", pt = "Holandês", ru = "Нидерландский",
        ko = "네덜란드어", cn = "荷兰语", mx = "Neerlandés", tw = "荷蘭語",
        nl = "Nederlands",
    }
    local alwaysNames = {
        en = "Always show", es = "Mostrar siempre", de = "Immer anzeigen",
        fr = "Toujours afficher", pt = "Sempre mostrar", ru = "Всегда показывать",
        ko = "항상 표시", cn = "始终显示", mx = "Mostrar siempre", tw = "始終顯示",
        nl = "Altijd tonen",
    }

    for lang, data in pairs(translations) do
        if data.options then
            if data.options.languages then
                data.options.languages["nl"] = data.options.languages["nl"] or nlLanguageNames[lang] or "Dutch"
            end
            if data.options.interactionModes then
                data.options.interactionModes["always"] = data.options.interactionModes["always"] or alwaysNames[lang] or "Always show"
            end
        end
    end
end

-- ─── TTS knop op het quest vertaalframe ─────────────────────────────────────
local ttsButton = nil
local lastTTSSoundHandle = nil

local function CreateTTSButton()
    if ttsButton then return end
    if not QuestTranslationFrame then return end

    ttsButton = CreateFrame("Button", "MultiLanguageNLTTSButton", QuestTranslationFrame)
    ttsButton:SetSize(24, 24)
    ttsButton:SetPoint("TOPRIGHT", QuestTranslationFrame, "TOPRIGHT", -8, -8)

    local icon = ttsButton:CreateTexture(nil, "ARTWORK")
    icon:SetAllPoints()
    icon:SetTexture("Interface\\Common\\VoiceChat-Speaker")
    ttsButton.icon = icon

    local highlight = ttsButton:CreateTexture(nil, "HIGHLIGHT")
    highlight:SetAllPoints()
    highlight:SetTexture("Interface\\Common\\VoiceChat-Speaker")
    highlight:SetVertexColor(0.2, 0.8, 1.0, 0.8)

    ttsButton:SetScript("OnEnter", function(self)
        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
        GameTooltip:SetText("Voorlezen (TTS)", 1, 1, 1)
        GameTooltip:AddLine("Klik om de quest tekst voor te laten lezen.", 0.7, 0.7, 0.7, true)
        GameTooltip:Show()
    end)
    ttsButton:SetScript("OnLeave", function()
        GameTooltip:Hide()
    end)

    ttsButton:SetScript("OnClick", function()
        local questID = GetQuestID()
        if not questID or questID == 0 then
            questID = C_QuestLog and C_QuestLog.GetSelectedQuest and C_QuestLog.GetSelectedQuest() or 0
        end

        if questID == 0 then
            print("|cff00ccff[ML_NL]|r Geen quest gevonden voor TTS.")
            return
        end

        if lastTTSSoundHandle then
            StopSound(lastTTSSoundHandle)
            lastTTSSoundHandle = nil
        end

        local soundFile = "Interface\\AddOns\\MultiLanguage_NL\\Audio\\q" .. questID .. ".ogg"
        local willPlay, soundHandle = PlaySoundFile(soundFile, "Master")

        if willPlay then
            lastTTSSoundHandle = soundHandle
            ttsButton.icon:SetVertexColor(0.2, 0.8, 1.0, 1.0)
            C_Timer.After(1, function()
                if ttsButton and ttsButton.icon then
                    ttsButton.icon:SetVertexColor(1, 1, 1, 1)
                end
            end)
        else
            print("|cff00ccff[ML_NL]|r Geen audio beschikbaar voor quest " .. questID)
        end
    end)
end

-- ─── Hook het quest vertaalframe om TTS knop te tonen/verbergen ─────────────
local function HookQuestTranslationFrame()
    if not QuestTranslationFrame then return end

    CreateTTSButton()

    QuestTranslationFrame:HookScript("OnShow", function()
        if ttsButton then
            if MultiLanguageOptions and MultiLanguageOptions.SELECTED_LANGUAGE == "nl" then
                ttsButton:Show()
            else
                ttsButton:Hide()
            end
        end
    end)

    QuestTranslationFrame:HookScript("OnHide", function()
        if ttsButton then
            ttsButton:Hide()
        end
    end)
end

-- ─── Slash commands ─────────────────────────────────────────────────────────
local function countTable(tbl)
    local n = 0
    if tbl then for _ in pairs(tbl) do n = n + 1 end end
    return n
end

local function fmtCount(nlCount, enCount)
    if enCount > 0 then
        return nlCount .. "/" .. enCount
    end
    return tostring(nlCount)
end

SLASH_MULTILANGUAGENL1 = "/mlnl"
SlashCmdList["MULTILANGUAGENL"] = function(msg)
    local qNL = countTable(MultiLanguageQuestData and MultiLanguageQuestData["nl"])
    local qEN = countTable(MultiLanguageQuestData and MultiLanguageQuestData["en"])
    local iNL = countTable(MultiLanguageItemData and MultiLanguageItemData["nl"])
    local iEN = countTable(MultiLanguageItemData and MultiLanguageItemData["en"])
    local sNL = countTable(MultiLanguageSpellData and MultiLanguageSpellData["nl"])
    local sEN = countTable(MultiLanguageSpellData and MultiLanguageSpellData["en"])
    local nNL = countTable(MultiLanguageNpcData and MultiLanguageNpcData["nl"])
    local nEN = countTable(MultiLanguageNpcData and MultiLanguageNpcData["en"])

    print("|cff00ccff[ML_NL]|r Nederlandse vertalingen geladen:")
    print("|cff00ccff[ML_NL]|r   Quests: " .. fmtCount(qNL, qEN))
    print("|cff00ccff[ML_NL]|r   Items:  " .. fmtCount(iNL, iEN))
    print("|cff00ccff[ML_NL]|r   Spells: " .. fmtCount(sNL, sEN))
    print("|cff00ccff[ML_NL]|r   NPCs:   " .. fmtCount(nNL, nEN))
end

-- ─── "Always show" modus: update vertaalframe bij quest selectie ─────────────
local alwaysModeFrame = CreateFrame("Frame")
alwaysModeFrame:RegisterEvent("QUEST_LOG_UPDATE")
alwaysModeFrame:SetScript("OnEvent", function()
    if MultiLanguageOptions and MultiLanguageOptions.SELECTED_INTERACTION == "always"
       and MultiLanguageOptions.QUEST_TRANSLATIONS
       and QuestMapDetailsScrollFrame and QuestMapDetailsScrollFrame:IsShown() then
        UpdateQuestTranslationFrame()
    end
end)

if QuestMapDetailsScrollFrame then
    QuestMapDetailsScrollFrame:HookScript("OnShow", function()
        if MultiLanguageOptions and MultiLanguageOptions.SELECTED_INTERACTION == "always"
           and MultiLanguageOptions.QUEST_TRANSLATIONS then
            C_Timer.After(0.1, function()
                UpdateQuestTranslationFrame()
            end)
        end
    end)
    QuestMapDetailsScrollFrame:HookScript("OnHide", function()
        if QuestTranslationFrame then
            QuestTranslationFrame:Hide()
        end
    end)
end

-- ─── Event handler ──────────────────────────────────────────────────────────
local function addonLoaded(self, event, addonLoadedName)
    if addonLoadedName == addonName then
        registerTranslations()
        injectIntoAllTranslations()
        addLanguageOption()
        addAlwaysInteraction()
        HookQuestTranslationFrame()

        local questCount = 0
        if MultiLanguageQuestData and MultiLanguageQuestData["nl"] then
            for _ in pairs(MultiLanguageQuestData["nl"]) do questCount = questCount + 1 end
        end

        print("|cff00ccff[ML_NL]|r Nederlandse taalpack geladen. " .. questCount .. " quests beschikbaar. /mlnl voor status.")
    end
end

optionsFrame:RegisterEvent("ADDON_LOADED")
optionsFrame:SetScript("OnEvent", addonLoaded)
