-- MultiLanguage_NL: Dutch language pack for MultiLanguage + TTS support
-- Author: Kenneth Audenaert

local addonName = ...
local ADDON_PREFIX = "MLNLTTS"

-- SavedVariables
MultiLanguageNLOptions = MultiLanguageNLOptions or {
    TTS_ENABLED = true,
}

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

-- ─── TTS knop op het quest vertaalframe ─────────────────────────────────────
local ttsButton = nil
local lastTTSQuestID = nil
local lastTTSSoundHandle = nil

local function CreateTTSButton()
    if ttsButton then return end
    if not QuestTranslationFrame then return end

    ttsButton = CreateFrame("Button", "MultiLanguageNLTTSButton", QuestTranslationFrame)
    ttsButton:SetSize(24, 24)
    ttsButton:SetPoint("TOPRIGHT", QuestTranslationFrame, "TOPRIGHT", -8, -8)

    -- Luidspreker icoon
    local icon = ttsButton:CreateTexture(nil, "ARTWORK")
    icon:SetAllPoints()
    icon:SetTexture("Interface\\Common\\VoiceChat-Speaker")
    ttsButton.icon = icon

    -- Highlight bij hover
    local highlight = ttsButton:CreateTexture(nil, "HIGHLIGHT")
    highlight:SetAllPoints()
    highlight:SetTexture("Interface\\Common\\VoiceChat-Speaker")
    highlight:SetVertexColor(0.2, 0.8, 1.0, 0.8)

    -- Tooltip
    ttsButton:SetScript("OnEnter", function(self)
        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
        GameTooltip:SetText("Voorlezen (TTS)", 1, 1, 1)
        GameTooltip:AddLine("Klik om de quest tekst voor te laten lezen.", 0.7, 0.7, 0.7, true)
        GameTooltip:Show()
    end)
    ttsButton:SetScript("OnLeave", function()
        GameTooltip:Hide()
    end)

    -- Klik handler: stuur TTS verzoek via whisper (real-time IPC)
    ttsButton:SetScript("OnClick", function()
        if not MultiLanguageNLOptions.TTS_ENABLED then
            print("|cff00ccff[ML_NL]|r TTS is uitgeschakeld. Gebruik /mlnl tts om in te schakelen.")
            return
        end

        local questID = GetQuestID()
        if not questID or questID == 0 then
            questID = C_QuestLog and C_QuestLog.GetSelectedQuest and C_QuestLog.GetSelectedQuest() or 0
        end

        if questID == 0 then
            print("|cff00ccff[ML_NL]|r Geen quest gevonden voor TTS.")
            return
        end

        -- Haal de NL tekst op voor TTS
        local questData = nil
        if MultiLanguageQuestData and MultiLanguageQuestData["nl"] then
            questData = MultiLanguageQuestData["nl"][questID]
        end

        if not questData then
            print("|cff00ccff[ML_NL]|r Geen Nederlandse vertaling beschikbaar voor deze quest.")
            return
        end

        -- Bepaal welke tekst voorgelezen moet worden
        local ttsText = questData.description or questData.completion or questData.objective
        if not ttsText or ttsText == "" then
            print("|cff00ccff[ML_NL]|r Geen tekst beschikbaar om voor te lezen.")
            return
        end

        -- Stop vorige TTS als die nog speelt
        if lastTTSSoundHandle then
            StopSound(lastTTSSoundHandle)
            lastTTSSoundHandle = nil
        end

        -- Speel OGG audio direct af via PlaySoundFile
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
            print("|cff00ccff[ML_NL]|r Geen audio beschikbaar voor quest " .. questID .. ". Voer precache.py --tts-ogg uit.")
        end

        lastTTSQuestID = questID
    end)
end

-- ─── Hook het quest vertaalframe om TTS knop te tonen/verbergen ─────────────
local function HookQuestTranslationFrame()
    if not QuestTranslationFrame then return end

    CreateTTSButton()

    -- Toon/verberg TTS knop met het vertaalframe
    QuestTranslationFrame:HookScript("OnShow", function()
        if ttsButton and MultiLanguageNLOptions.TTS_ENABLED then
            -- Toon alleen als NL geselecteerd is
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
SLASH_MULTILANGUAGENL1 = "/mlnl"
SlashCmdList["MULTILANGUAGENL"] = function(msg)
    local cmd = msg:lower():match("^%s*(%S+)") or ""
    if cmd == "tts" then
        MultiLanguageNLOptions.TTS_ENABLED = not MultiLanguageNLOptions.TTS_ENABLED
        print("|cff00ccff[ML_NL]|r TTS: " .. (MultiLanguageNLOptions.TTS_ENABLED and "AAN" or "UIT"))
    elseif cmd == "status" then
        local questCount = 0
        if MultiLanguageQuestData and MultiLanguageQuestData["nl"] then
            for _ in pairs(MultiLanguageQuestData["nl"]) do questCount = questCount + 1 end
        end
        print("|cff00ccff[ML_NL]|r NL vertalingen: " .. questCount .. " quests")
        print("|cff00ccff[ML_NL]|r TTS: " .. (MultiLanguageNLOptions.TTS_ENABLED and "AAN" or "UIT"))
        print("|cff00ccff[ML_NL]|r Selecteer 'Dutch' in MultiLanguage instellingen.")
    else
        print("|cff00ccff[ML_NL]|r Commando's: /mlnl tts | status")
    end
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

-- Hook quest selectie in de quest log (klik op een quest)
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
        addLanguageOption()
        addAlwaysInteraction()
        HookQuestTranslationFrame()

        local questCount = 0
        if MultiLanguageQuestData and MultiLanguageQuestData["nl"] then
            for _ in pairs(MultiLanguageQuestData["nl"]) do questCount = questCount + 1 end
        end

        print("|cff00ccff[ML_NL]|r Nederlandse taalpack geladen. " .. questCount .. " quests beschikbaar.")
        if questCount == 0 then
            print("|cff00ccff[ML_NL]|r Voer precache.py uit om vertalingen te genereren.")
        end
    end
end

optionsFrame:RegisterEvent("ADDON_LOADED")
optionsFrame:SetScript("OnEvent", addonLoaded)
