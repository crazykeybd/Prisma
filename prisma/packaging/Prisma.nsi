Unicode true
SetCompressor /SOLID lzma
SetCompressorDictSize 64

!include "MUI2.nsh"
!include "LogicLib.nsh"

!ifndef APP_VERSION
  !define APP_VERSION "0.1.0"
!endif
!ifndef APP_VERSION_QUAD
  !define APP_VERSION_QUAD "0.1.0.0"
!endif
!ifndef PAYLOAD_DIR
  !error "PAYLOAD_DIR is required"
!endif
!ifndef WEBVIEW2_SETUP
  !error "WEBVIEW2_SETUP is required"
!endif
!ifndef OUTPUT_FILE
  !error "OUTPUT_FILE is required"
!endif
!ifndef ICON_FILE
  !error "ICON_FILE is required"
!endif

Name "Prisma 多软件自动化测试平台"
OutFile "${OUTPUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\Prisma"
InstallDirRegKey HKCU "Software\Prisma" "InstallDir"
RequestExecutionLevel user

VIProductVersion "${APP_VERSION_QUAD}"
VIAddVersionKey /LANG=2052 "ProductName" "Prisma 多软件自动化测试平台"
VIAddVersionKey /LANG=2052 "CompanyName" "Prisma"
VIAddVersionKey /LANG=2052 "FileDescription" "Prisma 离线安装程序"
VIAddVersionKey /LANG=2052 "FileVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=2052 "ProductVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=2052 "LegalCopyright" "Copyright (C) 2026"

!define MUI_ICON "${ICON_FILE}"
!define MUI_UNICON "${ICON_FILE}"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\Prisma.exe"
!define MUI_FINISHPAGE_RUN_TEXT "启动 Prisma"
!define MUI_FINISHPAGE_LINK "安装目录：$INSTDIR"
!define MUI_FINISHPAGE_LINK_LOCATION "$INSTDIR"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
  SetShellVarContext current
  SetRegView 64
FunctionEnd

Function un.onInit
  SetShellVarContext current
  SetRegView 64
FunctionEnd

Section "Prisma" SEC_MAIN
  SetOutPath "$INSTDIR"

  ; Program code and the self-contained Python runtime are always updated.
  SetOverwrite on
  File "${PAYLOAD_DIR}\Prisma.exe"
  File "${PAYLOAD_DIR}\project_store.py"
  File "${PAYLOAD_DIR}\requirements.txt"
  File "${PAYLOAD_DIR}\README.md"
  File "${PAYLOAD_DIR}\测试用例表格填写规范.md"
  File /r "${PAYLOAD_DIR}\api"
  File /r "${PAYLOAD_DIR}\common"
  File /r "${PAYLOAD_DIR}\gui"
  File /r "${PAYLOAD_DIR}\keywords"
  File /r "${PAYLOAD_DIR}\run_prisma"
  File /r "${PAYLOAD_DIR}\software"
  File /r "${PAYLOAD_DIR}\python-runtime"
  File /r "${PAYLOAD_DIR}\config"

  ; User-editable resources are installed on first run and preserved on upgrade.
  SetOverwrite off
  File /r "${PAYLOAD_DIR}\data"
  File /r "${PAYLOAD_DIR}\inputs"
  File /r "${PAYLOAD_DIR}\software-library"
  File /r "${PAYLOAD_DIR}\projects"

  CreateDirectory "$INSTDIR\log"
  CreateDirectory "$INSTDIR\test_cases"
  CreateDirectory "$INSTDIR\exports"
  CreateDirectory "$INSTDIR\webview2-data"

  WriteUninstaller "$INSTDIR\Uninstall.exe"

  CreateDirectory "$SMPROGRAMS\Prisma"
  CreateShortcut "$SMPROGRAMS\Prisma\Prisma 多软件自动化测试平台.lnk" "$INSTDIR\Prisma.exe" "" "$INSTDIR\Prisma.exe" 0
  CreateShortcut "$SMPROGRAMS\Prisma\卸载 Prisma.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\Prisma 多软件自动化测试平台.lnk" "$INSTDIR\Prisma.exe" "" "$INSTDIR\Prisma.exe" 0

  WriteRegStr HKCU "Software\Prisma" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Prisma" "DisplayName" "Prisma 多软件自动化测试平台"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Prisma" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Prisma" "Publisher" "Prisma"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Prisma" "DisplayIcon" "$INSTDIR\Prisma.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Prisma" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Prisma" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Prisma" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Prisma" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Prisma" "NoRepair" 1

  ; The bundled WebView2 standalone installer makes setup work without network.
  ReadRegStr $0 HKLM "SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  ${If} $0 == ""
    ReadRegStr $0 HKCU "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  ${EndIf}
  ${If} $0 == ""
    DetailPrint "正在安装 Microsoft Edge WebView2 运行时..."
    SetOutPath "$PLUGINSDIR"
    File "/oname=MicrosoftEdgeWebView2Setup.exe" "${WEBVIEW2_SETUP}"
    ExecWait '"$PLUGINSDIR\MicrosoftEdgeWebView2Setup.exe" /silent /install' $1
    ${If} $1 != 0
      MessageBox MB_ICONEXCLAMATION|MB_OK "WebView2 运行时安装未成功（返回码 $1）。Prisma 已安装，但首次启动前可能需要补装 WebView2。"
    ${EndIf}
  ${EndIf}

  SetOutPath "$INSTDIR"
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\Prisma 多软件自动化测试平台.lnk"
  Delete "$SMPROGRAMS\Prisma\Prisma 多软件自动化测试平台.lnk"
  Delete "$SMPROGRAMS\Prisma\卸载 Prisma.lnk"
  RMDir "$SMPROGRAMS\Prisma"

  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Prisma"
  DeleteRegKey HKCU "Software\Prisma"

  ; Remove program files. User projects, imported software, data, inputs and logs
  ; are intentionally retained so an uninstall or upgrade cannot destroy them.
  Delete "$INSTDIR\Prisma.exe"
  Delete "$INSTDIR\project_store.py"
  Delete "$INSTDIR\requirements.txt"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\测试用例表格填写规范.md"
  Delete "$INSTDIR\gui_debug.log"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir /r "$INSTDIR\api"
  RMDir /r "$INSTDIR\common"
  RMDir /r "$INSTDIR\gui"
  RMDir /r "$INSTDIR\keywords"
  RMDir /r "$INSTDIR\run_prisma"
  RMDir /r "$INSTDIR\software"
  RMDir /r "$INSTDIR\python-runtime"
  RMDir "$INSTDIR\webview2-data"
  RMDir "$INSTDIR"
SectionEnd
