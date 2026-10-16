Obsidian is a text journaling app that lets users write ideas down with markdown as the base file type and lets users use graphs, canvas, and other plugins to help with their workflow. They let users pick a vault
as their default location for storing their project. These vaults require a sync if you want to use it across multiple devices. There are multiple syncing solutions in place already, using their paid version is likely
the best, but also costs money, which isn't ideal for some. They also offer iCloud as a sync option which is what this ObsidianSync project is based off of. 

The purpose for creating this project is to handle an annoying issue with iCloud being slow to update and not being inherently compatible with Windows PCs. The problem is that iCloud has a lag in it, so if you use the iCloud
sync, it will occasionally struggle to keep the sync going smoothly. For example, if you are writing a project and you set it up on your Windows PC, but you like to work on it from your phone at times that you're away from
your PC, then you have to deal with the lagging issue on the Windows side. The issue shows up when your vault is set to your Windows iCloud directory and you are writing a document, occasionally, it will hit the latency
issue and stop you from writing by creating a number of duplicates of the file you are working on (5+ duplicate pages of different progress) and it can slow down the writing process significantly. Instead, you can use 
ObsidianSync to handle the syncing issues for you.

The workflow looks like this:
  - Set your vault to a separate location from your iCloud. For example: C:\Users\name\Documents\ObsidianProjectName
  - Set up another version of the project on iCloud and enable the iCloud sync option. This would be in something like C:\Users\name\iCloud Drive\iCloud~md~obsidian\ObsidianProjectName
  - Set up ObsidianSync by pulling the repo and setting the config.ini with your updated pathways, local being the Windows location of your project, icloud being the iCloud location of your project.
  - Run the Sync using the Powershell script below:
      $dir = "C:\path\to\ObsidianSync"
      $py  = "$dir\.venv\Scripts\pythonw.exe"
      Test-Path $py, "$dir\main.py"
      
      $action = New-ScheduledTaskAction -Execute $py -Argument "`"$dir\main.py`""
      Set-ScheduledTask -TaskName "ObsidianSync" -Action $action
      Start-ScheduledTask ObsidianSync
      (Get-ScheduledTask ObsidianSync).State

  - To check the logs of the project, a check-sync.ps1 script is provided simply use Powershell and run .\check-sync.ps1 in the directory of ObsidianSync.
  - The Sync is now running.

The sync works by watching both directories and looking for the newest file info. On start up it will delay running for a minute before it checks both copies and copies the latest version. It's a two way sync so that
your project always stays up to date at the latest version of the project whether you work on it from your iPhone or from your Windows PC. This project is intended specifically for iCloud sync between an iPhone and a
Windows PC. It may not work as intended if used on other devices or using a different sync method.
